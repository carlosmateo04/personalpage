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
| TikTok | A draft in your TikTok inbox, or a private post | Public posts |

You can mix: connect what works directly, and tick "through the posting
service" for a platform where you prefer the service.

The redirect addresses below carry the port the app runs on: 8765, unless
something else on the computer already uses it, in which case the app takes
the next free one. The Accounts tab always shows the exact address to register.

## Claude

Claude picks the moments in each video and edits each clip. Two ways to reach it:

**An Anthropic API key.** Create an account at console.anthropic.com, add
credit, create an API key and paste it in the app. Works unattended.

**Amazon Bedrock.** Choose "Amazon Bedrock" and the AWS profile to sign in
with (the app lists the profiles on this computer). If the profile uses single
sign-on, the sign-in runs out after some hours and the app then says what to
run (`aws sso login --profile <name>`), so this way suits runs you start
yourself more than the daily run. Use an AWS account you are entitled to use
for this.

What is sent: frames of the video, what is said in it and its title. Model,
effort and what the channel wants ("direction") are in the same panel. The
panel shows the month's requests and an estimated cost; expect roughly one
request per video plus one per clip.

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
account is set to private). Posting publicly from an app needs TikTok's audit,
and TikTok's published criteria exclude tools that upload to their owner's own
accounts. There is no way around that with your own app. For hands-off public
posts on TikTok, use the posting service for TikTok.

1. Create an app at https://developers.tiktok.com/apps (its Sandbox is enough)
   and add **Login Kit** and **Content Posting API**.
2. In Login Kit choose **Desktop** and register the redirect address the app
   shows, exactly (`http://127.0.0.1:8765/oauth/tiktok/`).
3. Give the app the scopes `user.info.basic` and `video.upload` (drafts) or
   `video.publish` (private posts), add your TikTok account as a target user,
   and copy the client key and secret into the app.

Draft or private is chosen under Settings; connect again after changing it,
because the two need different permissions.

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
