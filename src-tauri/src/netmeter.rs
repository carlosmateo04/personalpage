//! What is actually leaving the machine.
//!
//! The bandwidth strip used to add up ffmpeg's own reported bitrates, which
//! answers a different question: it says how much this app *intended* to send,
//! and knows nothing about the Zoom call, the photo sync, or the backup that
//! is competing for the same uplink. Headroom computed that way is wrong
//! exactly when it matters — when something else is eating the pipe.
//!
//! So measure the interface instead. Every operating system keeps a cumulative
//! byte counter per interface; the rate is the difference between two readings
//! divided by the time between them. That figure includes our own streams and
//! everything else on the machine, which is what "how much upload is left"
//! actually depends on.
//!
//! What this cannot see is other devices on the same Wi-Fi. Nothing running on
//! this Mac can — that would need the router. Their effect shows up indirectly,
//! as our own streams failing to reach their target bitrate.

use serde::Serialize;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

/// One sample a second. Fast enough that a spike is visible while it is
/// happening, slow enough that a process spawn per tick is free.
const INTERVAL: Duration = Duration::from_secs(1);

/// How often to re-ask which interface carries the default route. Switching
/// from Wi-Fi to Ethernet, or a VPN coming up, changes the answer.
const IFACE_RECHECK: u32 = 15;

#[derive(Serialize, Clone, Debug)]
pub struct NetSample {
    /// The interface carrying the default route, e.g. `en0`.
    pub iface: String,
    /// Everything leaving the machine on it, this app included.
    pub up_kbps: u32,
    /// False when the platform gives no counters at all. The UI then falls
    /// back to the sum of our own streams, and says so rather than pretending.
    pub measured: bool,
}

// ------------------------------------------------------------------ parsing --

// The macOS readers are compiled out elsewhere, but their parsers stay
// covered by the tests on every platform — the format they handle is the
// fiddly part, and it should not go untested just because CI is not a Mac.

/// The interface carrying the default route, from `route -n get default`.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn parse_default_iface(out: &str) -> Option<String> {
    out.lines()
        .find_map(|l| l.split_once("interface:"))
        .map(|(_, v)| v.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Cumulative bytes sent on `iface`, from `netstat -ibn` output.
///
/// The column layout is not fixed — `Address` is blank on some rows, and the
/// trailing columns differ between macOS versions — so the position of
/// `Obytes` is derived from the header and counted from the *end* of the row.
/// Counting from the start breaks on the rows with no hardware address.
///
/// Per-address rows repeat the same counter and use `-` for the error columns,
/// so requiring the whole tail to be numeric keeps only the `<Link#n>` row and
/// makes double counting impossible.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn parse_netstat_tx(out: &str, iface: &str) -> Option<u64> {
    let mut lines = out.lines();
    let header: Vec<&str> = lines
        .by_ref()
        .find(|l| l.split_whitespace().next() == Some("Name"))?
        .split_whitespace()
        .collect();
    let from_end = header.len() - 1 - header.iter().position(|c| *c == "Obytes")?;

    lines
        .filter_map(|line| {
            let cols: Vec<&str> = line.split_whitespace().collect();
            if cols.first() != Some(&iface) || cols.len() <= from_end + 1 {
                return None;
            }
            let tail = &cols[cols.len() - from_end - 1..];
            if !tail.iter().all(|c| c.parse::<u64>().is_ok()) {
                return None;
            }
            tail[0].parse::<u64>().ok()
        })
        // Every matching row carries the same counter, so the maximum is that
        // counter and can never be a sum of duplicates.
        .max()
}

/// Cumulative bytes sent on `iface`, from the body of `/proc/net/dev`.
#[cfg(not(target_os = "macos"))]
pub fn parse_proc_net_dev_tx(out: &str, iface: &str) -> Option<u64> {
    out.lines().find_map(|line| {
        let (name, rest) = line.split_once(':')?;
        if name.trim() != iface {
            return None;
        }
        // Receive takes the first eight columns; transmitted bytes is the ninth.
        rest.split_whitespace().nth(8)?.parse().ok()
    })
}

// ------------------------------------------------------------------ reading --

#[cfg(target_os = "macos")]
mod sys {
    use std::process::Command;

    pub fn default_iface() -> Option<String> {
        let out = Command::new("/sbin/route")
            .args(["-n", "get", "default"])
            .output()
            .ok()?;
        super::parse_default_iface(&String::from_utf8_lossy(&out.stdout))
    }

    pub fn tx_bytes(iface: &str) -> Option<u64> {
        let out = Command::new("/usr/sbin/netstat")
            .args(["-ibn", "-I", iface])
            .output()
            .ok()?;
        super::parse_netstat_tx(&String::from_utf8_lossy(&out.stdout), iface)
    }
}

#[cfg(not(target_os = "macos"))]
mod sys {
    //! Linux, so the sampling loop can be exercised off a Mac. The shipped
    //! product is macOS only; this exists to keep the loop testable.

    pub fn default_iface() -> Option<String> {
        let table = std::fs::read_to_string("/proc/net/route").ok()?;
        table.lines().skip(1).find_map(|l| {
            let mut cols = l.split_whitespace();
            let name = cols.next()?;
            // Destination 00000000 is the default route.
            (cols.next()? == "00000000").then(|| name.to_string())
        })
    }

    pub fn tx_bytes(iface: &str) -> Option<u64> {
        let out = std::fs::read_to_string("/proc/net/dev").ok()?;
        super::parse_proc_net_dev_tx(&out, iface)
    }
}

// ------------------------------------------------------------------ sampling --

/// Turn two cumulative readings into a rate, or `None` when the pair cannot be
/// trusted: a different interface, a counter that went backwards because it
/// wrapped or the interface was reset, or no elapsed time.
///
/// `None` means "say nothing this tick", not "zero" — reporting zero on an
/// interface change would draw a dropout that never happened.
pub fn rate_kbps(prev: Option<(&str, u64)>, now: (&str, u64), secs: f64) -> Option<u32> {
    let (prev_iface, prev_tx) = prev?;
    let (iface, tx) = now;
    if prev_iface != iface || tx < prev_tx || secs <= 0.0 {
        return None;
    }
    Some((((tx - prev_tx) as f64 * 8.0) / secs / 1000.0).round() as u32)
}

/// Start sampling for the life of the process.
pub fn spawn(app: AppHandle) {
    std::thread::spawn(move || {
        let mut prev: Option<(String, u64, Instant)> = None;
        let mut iface: Option<String> = None;
        let mut ticks: u32 = 0;

        // The first reading is taken straight away and only establishes a
        // baseline; sleeping first would leave the strip blank for two seconds
        // at launch instead of one.
        loop {
            if iface.is_none() || ticks % IFACE_RECHECK == 0 {
                let found = sys::default_iface();
                if found != iface {
                    // A new interface means a new counter; the old reading is
                    // not comparable to the next one.
                    prev = None;
                    iface = found;
                }
            }
            ticks = ticks.wrapping_add(1);

            let reading = iface
                .as_deref()
                .and_then(|i| sys::tx_bytes(i).map(|tx| (i.to_string(), tx)));

            match reading {
                Some((name, tx)) => {
                    let now = Instant::now();
                    let secs = prev
                        .as_ref()
                        .map(|(_, _, at)| now.duration_since(*at).as_secs_f64())
                        .unwrap_or(0.0);
                    let rate = rate_kbps(
                        prev.as_ref().map(|(i, t, _)| (i.as_str(), *t)),
                        (&name, tx),
                        secs,
                    );
                    if let Some(up_kbps) = rate {
                        let _ = app.emit(
                            "net:sample",
                            NetSample {
                                iface: name.clone(),
                                up_kbps,
                                measured: true,
                            },
                        );
                    }
                    prev = Some((name, tx, now));
                }
                None => {
                    // No default route, or no counters. Say so every tick
                    // rather than leaving a stale number on screen looking
                    // authoritative.
                    let _ = app.emit(
                        "net:sample",
                        NetSample {
                            iface: String::new(),
                            up_kbps: 0,
                            measured: false,
                        },
                    );
                    prev = None;
                }
            }

            // Unconditional, and at the end: an early `continue` on the
            // failure path would turn this into a busy loop.
            std::thread::sleep(INTERVAL);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    // Verbatim `netstat -ibn` from macOS 14. Note the blank Address on the
    // loopback link row and the `-` placeholders on the per-address rows.
    const NETSTAT: &str = "\
Name  Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0   16384 <Link#1>                        128231     0   35817241   128231     0   35817241     0
lo0   16384 127           127.0.0.1         128231     -   35817241   128231     -   35817241     -
lo0   16384 ::1/128     ::1                 128231     -   35817241   128231     -   35817241     -
en0   1500  <Link#11>   a4:83:e7:1c:2d:3e  4821993     0 5219847362  2911204     0 1839472011     0
en0   1500  192.168.1     192.168.1.42     4821993     - 5219847362  2911204     - 1839472011     -
utun3 1380  <Link#17>                          912     0     114221      904     0      98220     0";

    #[test]
    fn reads_the_link_row_for_the_named_interface() {
        assert_eq!(parse_netstat_tx(NETSTAT, "en0"), Some(1_839_472_011));
        assert_eq!(parse_netstat_tx(NETSTAT, "lo0"), Some(35_817_241));
        assert_eq!(parse_netstat_tx(NETSTAT, "en5"), None);
    }

    #[test]
    fn tolerates_a_missing_hardware_address() {
        // utun and lo0 link rows have no Address column at all; counting the
        // columns from the left would read the wrong field for them.
        assert_eq!(parse_netstat_tx(NETSTAT, "utun3"), Some(98_220));
    }

    #[test]
    fn tolerates_an_extra_trailing_column() {
        // Some releases add Drop after Coll. Deriving the offset from the
        // header keeps Obytes correct without knowing which version this is.
        let out = "\
Name  Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll  Drop
en0   1500  <Link#11>   a4:83:e7:1c:2d:3e  4821993     0 5219847362  2911204     0 1839472011     0     0";
        assert_eq!(parse_netstat_tx(out, "en0"), Some(1_839_472_011));
    }

    #[test]
    fn a_repeated_counter_is_never_summed() {
        // Three rows for en0 all report the same cumulative total. Adding them
        // would treble the reported bandwidth.
        assert_eq!(parse_netstat_tx(NETSTAT, "en0"), Some(1_839_472_011));
    }

    #[test]
    fn no_header_means_no_reading() {
        assert_eq!(parse_netstat_tx("netstat: command not found", "en0"), None);
    }

    #[test]
    fn finds_the_default_interface() {
        let out = "\
   route to: default
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>";
        assert_eq!(parse_default_iface(out), Some("en0".into()));
        assert_eq!(parse_default_iface("route: writing to routing socket: not in table"), None);
    }

    #[test]
    fn a_rate_needs_two_comparable_readings() {
        // 1 MB in one second is 8000 kbps.
        assert_eq!(rate_kbps(Some(("en0", 1_000_000)), ("en0", 2_000_000), 1.0), Some(8_000));
        // Half the time, twice the rate.
        assert_eq!(rate_kbps(Some(("en0", 1_000_000)), ("en0", 2_000_000), 0.5), Some(16_000));
        // Nothing sent.
        assert_eq!(rate_kbps(Some(("en0", 5_000)), ("en0", 5_000), 1.0), Some(0));
    }

    #[test]
    fn discontinuities_report_nothing_rather_than_zero() {
        // First sample of the process.
        assert_eq!(rate_kbps(None, ("en0", 1_000), 1.0), None);
        // Wi-Fi to Ethernet: a different counter entirely.
        assert_eq!(rate_kbps(Some(("en0", 9_000_000)), ("en5", 12_000), 1.0), None);
        // The counter wrapped or the interface was reset. Treating this as a
        // delta would draw a spike of hundreds of Gbps.
        assert_eq!(rate_kbps(Some(("en0", 4_294_000_000)), ("en0", 12_000), 1.0), None);
        // No elapsed time: the division is meaningless.
        assert_eq!(rate_kbps(Some(("en0", 1_000)), ("en0", 2_000), 0.0), None);
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn reads_proc_net_dev() {
        let out = "\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  123456     789    0    0    0     0          0         0   123456     789    0    0    0     0       0          0
  eth0: 5219847362 4821993    0    0    0     0          0         0 1839472011 2911204    0    0    0     0       0          0";
        assert_eq!(parse_proc_net_dev_tx(out, "eth0"), Some(1_839_472_011));
        assert_eq!(parse_proc_net_dev_tx(out, "eth1"), None);
    }
}

