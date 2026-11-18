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

> Daily GTA 6 clips from streams. Unofficial fan channel, not affiliated with
> Rockstar Games or Take-Two. Clips posted with creator permission. Paid
> partnerships are labelled.

YouTube channel description:

> The best GTA 6 moments from streamers, clipped daily: chases, fails, glitches
> and reactions. Unofficial fan channel, not affiliated with Rockstar Games or
> Take-Two Interactive. Clips are posted with the creator's permission; paid
> partnerships are disclosed on every video.

## 2. Posting service (about 15 minutes)

1. Sign up at upload-post.com and pick a paid plan that allows scheduled video
   uploads for one profile.
2. Create one profile (for example `leonidaclips`) and connect the four accounts.
3. Create an API key. Do not paste it into chat: tell Claude "the key is ready"
   and enter it in the secure form that opens.

## 3. Footage you are allowed to clip

Pick one:

- **A paid clipping campaign.** Join through contentrewards.com or vyro.com only
  (never pay to join). Send Claude the brief: which streamer, where the footage
  is, the required hashtags and disclosure wording, and the view rules.
- **Your own gameplay.** Record it and drop the files into `clipper/inbox/`, or
  give a link to your own channel.

Payout marketplaces also ask for identity verification before they pay.

## 4. Tell Claude to go live

Claude then fills in `config.toml` (profile, campaign, sources), asks you to
approve the network hosts the footage needs, runs `doctor`, and switches
`publish.mode` to `live`. From then on the daily run needs nothing from you
except submitting each day's post links to the campaign (they are listed in
`reports/latest.md`).
