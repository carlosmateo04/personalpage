# Accounts: Claude and the four platforms

Everything here is set up in the app, on the **Accounts** tab. Keys and
sign-ins are typed there, never into a chat or a file in this repository. They
are kept in the Windows Credential Manager (elsewhere: a file only you can
read) and are never shown again.

Each platform section below starts with what that platform lets an app do that
its own user registered. Those are the platforms' rules as their documentation
read on 20 November 2026; they decide what "connected directly" can mean.

| | Directly, with your own app | Through the posting service (Upload-Post, paid) |
|---|---|---|
| YouTube | Public uploads, scheduled by YouTube itself | Yes |
| Facebook | Page reels and videos, scheduled by Facebook itself | Yes |
| Instagram | Reels, sent when their time comes (this computer must be on) | Yes |
| TikTok | A draft in your TikTok inbox, a private post, or (once TikTok has audited your app) a public post | Public posts |

You can mix: connect what works directly, and tick "through the posting
service" for a platform where you prefer the service.

The redirect addresses below carry the port the app runs on: 8765, unless
something else on the computer already uses it, in which case the app takes
the next free one. The Accounts tab always shows the exact address to register.

## Claude

Claude picks the moments in each video and edits each clip. You can save
several Claude accounts and switch between them: under Accounts with "Use this
one", or from the "Claude" chip at the top of every tab. The one in use is
marked; "Test" asks Claude one free question with it. "Remove" deletes the
account's key or sign-in from this computer.

Three kinds of account:

**Sign in with Claude.** Press the button, give the account a name if you
like, and approve on Anthropic's sign-in page in the browser tab that opens.
The app notices by itself and starts using it. To add a second account, press
the button again and sign in with that one in the tab. The sign-in is to a
**Claude Console account** (console.anthropic.com), the kind that is billed for
what it uses. Anthropic does not let other apps use a Claude.ai chat
subscription (Pro or Max), so that kind of account cannot be signed in here.
The sign-in is done by Anthropic's own tool (`ant`), which the Windows
installer adds; if the button says the tool is missing, run the installer
again. The sign-in renews itself.

**An API key.** Create a key at console.anthropic.com (the account needs
credit), choose "Add an API key" and paste it. It is tested before it is
saved. Works unattended.

**Amazon Bedrock.** Choose "Add Amazon Bedrock" and the AWS profile to sign in
with (the app lists the profiles on this computer). If the profile uses single
sign-on, the sign-in runs out after some hours and the app then says what to
run (`aws sso login --profile <name>`), so this way suits runs you start
yourself more than the daily run. Use an AWS account you are entitled to use
for this.

What is sent: frames of the video, what is said in it and its title. Model
and what the channel wants ("direction") are in the same panel. The panel
shows the month's requests and an estimated cost; expect roughly one request
per video plus one per clip.

## YouTube

**What it allows:** public uploads from your own Google project (since
2 November 2026 they are no longer forced to private), 100 uploads a day.
YouTube holds a clip until its posting time itself.

One thing to know before choosing clip length: YouTube blocks a Short longer
than 60 seconds worldwide when a copyright owner has a claim on it, and music
playing in a game is a common cause. Clips of a minute or less are not blocked
for a claim.

1. Signed in with the Google account that owns the channel, open
   https://console.cloud.google.com/auth/overview/personal and create the app in
   **Personal mode**. Personal mode needs no review, shows no warning and its
   sign-in does not run out; it works only for the Google account that created
   it. (If the channel belongs to another account or a Brand Account, create an
   "External" app and publish it instead; "Testing" would make you sign in
   again every seven days.)
2. Enable **YouTube Data API v3** for the project.
3. Create an OAuth client of the type **Desktop app**. Copy its client ID and
   secret into the app and press Connect. Nothing has to be registered for the
   redirect.

The app asks for two permissions: uploading videos and reading your channel
(for the channel name, the status of uploads and their view counts). Disconnect
in the app withdraws the access at Google as well.

## TikTok

**What it allows:** an app registered by its own user can deliver a video to
your TikTok inbox as a **draft**, which you open in the TikTok app, add the
caption to and post (the caption cannot be sent along; five drafts may wait at
a time), or post **privately** (only you can see it, and only while the
account is set to private). Posting publicly from an app needs TikTok's audit
of that app, and TikTok's published criteria leave out tools that upload to
their owner's own accounts.

Clipper posts through TikTok's official service only. It does not imitate the
TikTok app or drive a signed-in browser to get around the audit: that breaks
TikTok's terms, is what its bot detection looks for, and puts the account you
want to earn with at risk.

That leaves four ways to a TikTok post, chosen under Settings and on each clip:

| Way | What you do | Public? |
|---|---|---|
| Draft (default) | Open the notification in the TikTok app, paste the caption, post | Yes, by your tap |
| By hand, from the clip | "Post on TikTok by hand": Clipper copies the caption, shows the file and opens TikTok's upload page; then "Mark as posted" | Yes |
| The posting service | Nothing; tick "TikTok through the posting service" | Yes, hands-off |
| Post with the caption ("public") | Nothing, once TikTok has audited your app | Yes, hands-off |
| Private | Nothing | No: only you see it |

With "public", each clip has its own **TikTok post options**: who can watch
(from what the account offers), comments, duets, stitches, and "promotes my
own brand". The defaults are under Settings. Before every post Clipper asks
TikTok what the account allows, as TikTok requires, and never asks for more.
From an app that has not been audited TikTok refuses a public post, and the
clip then says so and offers the other ways.

"Mark as posted" keeps the clip's record straight (and stops Clipper from
sending it to TikTok as well); the clip can still be sent to the other
platforms afterwards.

1. Create an app at https://developers.tiktok.com/apps (its Sandbox is enough)
   and add **Login Kit** and **Content Posting API**.
2. In Login Kit choose **Desktop** and register the redirect address the app
   shows, exactly (`http://127.0.0.1:8765/oauth/tiktok/`).
3. Give the app the scopes `user.info.basic` and `video.upload` (drafts) or
   `video.publish` (private or public posts), add your TikTok account as a
   target user, and copy the client key and secret into the app.

After changing between draft and posting under Settings, connect again: the
two need different permissions, and the clip says so if you forget.

## Facebook and Instagram

**What it allows:** one Facebook sign-in connects your Page and the Instagram
professional account linked to it. An app used only by people with a role on
it needs no App Review. Facebook holds a clip until its posting time; Instagram
has no scheduling, so the clip is sent when its time comes. Page reels stop at
90 seconds, so a longer clip goes up as a Page video.

What Meta's documentation leaves open: whether posts made by an app that has
not been reviewed are visible to everyone. Its one statement is that content
made by an app in development mode is seen only by people with a role on the
app. Check the first post from a browser where you are signed out.

1. Create an app at https://developers.facebook.com/apps with the use cases for
   managing a Page and for Instagram. You are its administrator.
2. Under Facebook Login, add the redirect address the app shows
   (`http://localhost:8765/oauth/meta`) to the valid OAuth redirect addresses.
3. Copy the app ID and app secret into the app. If the app uses Facebook Login
   for Business, also paste its configuration ID.
4. Your Instagram account must be a professional account linked to the Page.

If the sign-in page does not come back to the app, create a user token for the
same app in Meta's Graph API Explorer with the permissions `pages_show_list`,
`pages_read_engagement`, `pages_manage_posts`, `instagram_basic` and
`instagram_content_publish`, and paste it under "paste a token instead".

The sign-in lasts about 60 days; the app shows the date and asks you to
connect again before it.

## The posting service (optional)

Upload-Post is a paid service whose apps the platforms have approved. Create
an account at upload-post.com, connect your social accounts to a profile
there, create an API key and paste it in the app. It is used for every chosen
platform that is not connected directly, and for those you tick "through the
posting service".

## When posts go out

Each clip has a posting time. YouTube and Facebook are handed the clip
straight away and publish it at that time themselves. TikTok and Instagram are
sent at that time by this computer: by the app while it is open, and by the
Windows task "Clipper posting", which the daily switch turns on and which
looks every 15 minutes. If the computer was off, they go out when it is next
on. A failed post is tried again by itself when that can help (no connection,
a daily limit); otherwise the clip shows what is wrong and a "Try again"
button. Nothing is ever sent twice.

## The cut each platform gets

A clip is made once, at the length set under Settings (about 1:30). Each
platform then gets the version its rules ask for (Settings, "The cut each
platform gets"):

| Platform | Default | Why |
|---|---|---|
| TikTok | at least 61 seconds | Its Creator Rewards Program only counts videos longer than one minute |
| YouTube | at most 59 seconds | A Short of more than 60 seconds is blocked worldwide when a copyright owner claims something in it (music in the game, for one). Shorts may run to 3 minutes: set 180 to accept that |
| Facebook | at most 90 seconds | Page reels stop there; longer goes up as a plain Page video |
| Instagram | at most 180 seconds | Longer reels are not recommended to new viewers |

Where the clip is longer than a platform's maximum, Clipper renders a second
version around the moment the clip builds to, with the same headline and its
own captions and cover, and sends that platform the short one. The clip card
lists the versions and plays each. A clip shorter than a platform's minimum is
not sent there, and the card says why. 0 means no limit.

These are format rules. Being paid also depends on each platform accepting the
videos as original work: TikTok's programme asks for original videos, YouTube
excludes reused content, Facebook's programme is by invitation. Cutting,
captions and headlines do not make someone else's gameplay original in the
platforms' eyes; your own gameplay, or footage you have permission to use with
something of your own added, is what those rules are about. See
`reports/GTA 6 paid clipping feasibility.md`.

## Rules for being paid

Length is one rule of several. These are the others, as the platforms' pages
and press reports of their updates had them on 20 November 2026, and what
Clipper does about each. They are applied when a clip is cut and rendered, and
every finished clip shows a **Monetization check** per platform on its card.
Settings are under Settings, "Rules for being paid".

| Rule | Whose | What Clipper does |
|---|---|---|
| No graphic violence at the start | YouTube: in the first 7 seconds no ads, in seconds 8 to 15 limited ads (September 2026). Graphic means brutal killings or severe injuries shown close; ordinary shooting, crashes and fights do not count | Claude marks such moments. The start of the clip, and of each platform's own version, is moved so that none falls in the first 15 seconds. The cover is never taken from one |
| Never paid at any point | All: sexual content, sexual violence, torture, hate-motivated attacks, violence against children, attacks on real named people | Claude drops the clip |
| Strong language | YouTube (titles, thumbnails, heavy use), Meta (a restricted category) | Masked in captions and titles; silenced in the sound |
| No other app's mark | TikTok (not shown to new viewers), Instagram, Facebook: another platform's or channel's watermark, logo or QR code | Claude drops the clip |
| Music | All: its owner can claim it; the ad money then goes to them, or the clip is muted or blocked. YouTube blocks a claimed Short over a minute | Heard by a small model on this computer and reported with how long it plays. A setting holds such clips back. YouTube's version is kept under a minute |
| A moving picture | Facebook, Instagram: static, looping and slideshow videos are not paid | A clip that is mostly a still picture is not sent |
| A sharp picture | TikTok, Instagram: blurry video is recommended less | Reported when the source is under 720 lines |
| Hashtags | Instagram: five at most (since December 2025); Facebook: few | Five at most on both; checked again after an edit by hand |
| No asking for likes | Facebook, Instagram: "like and share", "follow for more" | Never written; flagged if typed in |
| Paid posts | TikTok: sponsored videos earn nothing from the Creator Rewards Program | Reported |
| **Originality** | All four: reposted footage is not paid, and captions, headlines, cropping and your own watermark do not make it original | **No edit meets this.** Reported on every clip. Your own gameplay, or your own commentary on the clip, is what the rule asks for |

How to read the check: green means the rule is met; yellow means a risk, or
that nobody could check (the two rules that need eyes are only checked when
Claude is set up, and music only when the listening model is installed, which
the installer adds); red means the clip breaks that platform's rule, and it is
then not sent there unless you switch that off.

What the music check cannot do: it hears that music plays, not whose it is.
The game's own score and a licensed radio track sound alike to it. What the
violence check cannot do: it relies on Claude seeing the moment in a sheet of
sixteen frames, so a moment that lasts a fraction of a second can be missed.
