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

> Daily GTA 6 clips: new findings, tricks, glitches and highlights. Unofficial
> fan channel, not affiliated with Rockstar Games or Take-Two. Every clip credits
> its creator. Creators: message us to have a clip removed.

YouTube channel description:

> The best GTA 6 gameplay clips, sorted daily: new findings, tricks, glitches and
> highlights. Pure gameplay, no commentary. Unofficial fan channel, not affiliated with Rockstar Games or Take-Two
> Interactive. Every clip credits the creator it came from. Creators: message us
> and we will remove any clip of yours.

## 2. Connect the accounts for posting

Posting no longer needs a service. In the app, under **Accounts**, connect
YouTube, Facebook and Instagram, and TikTok each with an app you register at
that platform, free of charge. [ACCOUNTS.md](ACCOUNTS.md) has the steps and,
for each platform, what it allows such an app to do. In short: YouTube and
Facebook post publicly and on schedule by themselves; Instagram posts when its
time comes; TikTok only takes a draft or a private post from your own app
(you make the draft public by posting it in the TikTok app, and each clip also
has "Post on TikTok by hand").

The posting service is still there as an option, per platform. It is the way
to hands-off public posts on TikTok:

1. Sign up at upload-post.com and pick a paid plan that allows scheduled video
   uploads for one profile.
2. Create one profile (for example `leonidaclips`) and connect the accounts.
3. Create an API key and paste it in the app under Accounts, never into a chat.

## 3. Footage: where it comes from and where the run happens

`config.toml` searches YouTube for this week's GTA 6 videos labelled "no
commentary" (general gameplay, chases and stunts, easter eggs, how-to guides,
glitches). Videos with a streamer on camera or talking over the game are left
out automatically.

Two things only you can settle:

- **Where the run happens.** YouTube refuses downloads from cloud machines, so
  the daily run has to happen on a home internet connection. `LOCAL.md` has
  the steps for running it on your own computer, by hand or with a Claude Code
  session there. The alternative is to record your own gameplay and drop the
  files into `clipper/inbox/`.
- **Permission.** None is on file for the channels the searches find. Clips
  credit the channel, but credit is not permission: a creator can have a clip
  taken down, and repeated takedowns close an account. To record a basis for a
  channel, add a source for it with `permission` filled in.

## 4. Tell Claude to go live

Claude then fills in the posting profile in `config.toml`, runs `doctor`, and
switches `publish.mode` to `live`. From then on the daily run grabs, cuts, sorts
and schedules the clips by itself. `reports/latest.md` shows what went out and
what was not fetched.
