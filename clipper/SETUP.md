# Going live: the steps only you can do

The pipeline, the daily schedule and the brand images are ready. These steps need
your phone, your identity or your money, so they cannot be automated.

## 1. Create the four accounts (about 20 minutes)

Suggested name: **Leonida Clips**. Use the same handle everywhere if it is free.
YouTube requires a fan channel to say so in its name or handle, so keep "fan"
or "clips" plus the bio line below.

| Platform | Handle to try | Notes |
|---|---|---|
| YouTube | `@LeonidaClipsFan` | Verify the phone number so uploads are not capped |
| TikTok | `@leonidaclips` | Keep the account public; private accounts cannot post publicly through the API |
| Instagram | `@leonidaclips` | Switch to a Professional (Creator) account |
| Facebook | Page "Leonida Clips" | A Page, not a personal profile; link it to the Instagram account |

Other names if those are taken: **Vice Coast Clips**, **Port Gellhorn Daily**.

Avatar and banner: `assets/brand/avatar.png`, `assets/brand/banner.png`.
For a different name:

```bash
./brand.sh "Your Name" "UNOFFICIAL FAN CHANNEL"
```

Bio (all platforms):

> Daily GTA 6 clips: new findings, tricks, glitches and wild moments. Unofficial
> fan channel, not affiliated with Rockstar Games or Take-Two. Every clip credits
> its creator. Creators: message us to have a clip removed.

YouTube channel description:

> The best GTA 6 clips, sorted daily: new findings, tricks, glitches and wild
> moments. Unofficial fan channel, not affiliated with Rockstar Games or Take-Two
> Interactive. Every clip credits the creator it came from. Creators: message us
> and we will remove any clip of yours.

## 2. Posting service (about 15 minutes)

1. Sign up at upload-post.com and pick a paid plan that allows scheduled video
   uploads for one profile.
2. Create one profile (for example `leonidaclips`) and connect the four accounts.
3. Create an API key. Do not paste it into chat: tell Claude "the key is ready"
   and enter it in the secure form that opens.

## 3. Choose whose footage to clip

`config.toml` starts with six Twitch channels (their most-viewed GTA 6 clips of
the day) and three YouTube searches. Edit the `[[sources]]` list to add or
remove channels.

No permission is on file for any of the starter channels. Clips credit the
channel, but credit is not permission: a creator can have a clip taken down,
and repeated takedowns close an account. For each channel you keep, check its
clip policy (channel panels, Discord) and write it in `permission`, or join its
clipping campaign.

## 4. Tell Claude to go live

Claude then fills in the posting profile in `config.toml`, runs `doctor`, and
switches `publish.mode` to `live`. From then on the daily run grabs, cuts, sorts
and schedules the clips by itself. `reports/latest.md` shows what went out and
what was not fetched.
