# Technical and policy limits on automating a short-form clip channel (state as of 17 November 2026)

Research-method note for the report writer: direct fetches of the official documentation hosts (developers.google.com, developers.tiktok.com, developers.facebook.com, docs.x.com, dev.twitch.tv) were blocked by this session's network allowlist. Where an official page is cited below, the content comes from search-engine extracts of that page, not from a full read of it. Those items are marked "(official page, via search extract)". Items from vendor blogs are marked "(vendor/secondary)" and items from forums or GitHub threads are marked "(anecdote)". Anything marked UNCONFIRMED should be re-checked before it is stated as fact.

## 1. YouTube Data API v3: quota, upload cost, private-lock rule, audits, channel limits, Shorts

### Takeaway
Upload quota stopped being the bottleneck in 2025-2026 (the cost fell from about 1,600 units to about 100 units on 4 December 2025, then uploads moved to their own bucket of 100 calls/day on 1 June 2026). The binding limits are now (a) the rule that uploads from an unaudited API project are locked to private, and (b) an unpublished per-channel daily upload cap that is lowest for new, unverified channels.

### Cited Findings
- The `videos.insert` reference page states a quota impact of 100 calls per day, each call costing 1 unit in the "Video Uploads" quota bucket (official page, via search extract) — [YouTube Data API: Videos: insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- Revision history, 1 June 2026: YouTube is moving to a granular quota system, starting with `videos.insert` and `search.list`, which are now charged to their own buckets while other methods stay in the existing bucket (official page, via search extract) — [YouTube Data API Revision History](https://developers.google.com/youtube/v3/revision_history?hl=en)
- Revision history, 4 December 2025: the documented upload cost changed from approximately 1,600 units to approximately 100 units (official page, via search extract) — [YouTube Data API Revision History](https://developers.google.com/youtube/v3/revision_history?hl=en)
- The general default allocation for other methods remains 10,000 units per day, and quota above the default requires a compliance audit (official page, via search extract) — [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits?hl=en)
- Older guides still describe 1,600 units per upload out of 10,000 (about 6 uploads/day); those are out of date — [Ayrshare](https://www.ayrshare.com/solutions/how-to-bypass-the-youtube-10k-unit-limit-fixing-the-403-quota-exceeded-error/); [bundle.social](https://bundle.social/blog/youtube-api-quota-exceeded-limits-fixes)
- Private-lock rule: videos uploaded via `videos.insert` from unverified API projects created after 28 July 2020 are restricted to private viewing mode; to lift the restriction the project must pass an audit verifying compliance with the Terms of Service (official page, via search extract) — [Videos: insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- A video already locked private this way cannot be appealed; it must be re-uploaded through a verified service or through the YouTube app/site (vendor quoting YouTube Help; not checked against the Help page) — [Ayrshare](https://www.ayrshare.com/solutions/google-api-error-403-unverified-app-how-to-fix-the-audit-pipeline/)
- Audit process: the developer submits the "YouTube API Services - Audit and Quota Extension Form"; the docs say a member of the API Services team will make contact "as soon as possible" and give no service-level time. A shorter re-audit form exists for projects audited in the last 12 months, and a separate Appeals Form covers failed audits (official pages, via search extract) — [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits?hl=en); [Audit and Quota Extension Form](https://support.google.com/youtube/contact/yt_api_form?hl=en)
- Channel-level cap: YouTube limits how many videos a channel can upload in a 24-hour period across desktop, mobile and the API, and does not publish the number. The API returns `uploadLimitExceeded` (HTTP 400) when the channel cap is hit even if project quota remains. YouTube's advice is to retry in 24 hours — [YouTube Help: Common uploading errors](https://support.google.com/youtube/answer/10383400?hl=en); [postpeer error reference](https://www.postpeer.dev/errors/youtube)
- YouTube's feature-access table lists "Upload videos and Shorts" with a "limited daily limit" for default channels and a "higher daily limit" for channels with advanced features, which come from channel history or identity verification (secondary summary of the Help table) — [adaptlypost](https://adaptlypost.com/blog/youtube-upload-limit-per-day)
- Community Guidelines strikes reduce how much a channel can upload — [YouTube Help: Common uploading errors](https://support.google.com/youtube/answer/10383400?hl=en)
- Unverified channels are limited to 15-minute uploads; phone verification (SMS or automated voice call) raises this to 12 hours or 256 GB (secondary) — [quso.ai](https://quso.ai/blog/youtube-uploading-limits.html)
- Shorts classification is by format, not by a separate upload path: 3 minutes or less and square or taller aspect ratio; a horizontal video under 3 minutes stays a regular video. The `#shorts` hashtag is described as optional in 2026 (secondary) — [OpenClip](https://openclip.app/guides/youtube-shorts-length); [ShortSync](https://shortsync.app/resources/youtube-shorts-upload-requirements-2026)

### Inferences
- Uploads per day allowed by quota: about 6/day per project until 4 December 2025; about 100/day from then until 1 June 2026; 100/day (dedicated bucket) since 1 June 2026. A clip channel posting 3-10 Shorts a day is nowhere near the quota.
- A solo operator's own script (own Google Cloud project) can upload, but every video lands as private until the project passes the compliance audit. The audit is designed for products serving third-party users, and no turnaround time is published, so a personal script should be assumed unable to publish publicly on day one.
- Shorts can be uploaded through `videos.insert` like any other video; no Shorts-specific endpoint or flag was found in any source. The vertical format and length decide the classification.
- The realistic daily ceiling for a new channel is the unpublished channel cap, not the API.

### Gaps
- Audit duration: no official figure found. Community reports of weeks-to-months exist in general, but none was retrieved in this session, so no number is given.
- OAuth consent-screen verification was not confirmed from an official page in this session. Unsourced background knowledge (verify before use): `youtube.upload` is a sensitive scope; an app left in "Testing" status is limited to 100 test users and its refresh tokens expire after 7 days; moving to "Production" for external users triggers Google's OAuth app verification (brand and sensitive-scope review), which is separate from the YouTube API compliance audit.
- The exact numeric channel daily upload cap for new versus advanced-feature channels is not published.
- Whether the 100 calls/day bucket applies identically to projects created before June 2026 was not confirmed.

## 2. TikTok Content Posting API: direct post vs inbox upload, audit, caps

### Takeaway
An unaudited TikTok app can only post private (SELF_ONLY) videos, for at most 5 users per 24 hours, and TikTok has rejected audit applications from apps that post to the operator's own single account ("personal or internal use"). For a solo operator, own-app direct public posting is in practice not available; the options are inbox/draft upload finished by hand in the app, or an already-audited third-party scheduler.

### Cited Findings
- All content posted by unaudited clients is restricted to private viewing mode (official page, via search extract) — [TikTok Direct Post reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- Unaudited API clients can let up to 5 users post in a 24-hour window, and all those accounts must be set to private at the time of posting (official page, via search extract) — [TikTok Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)
- A per-creator cap exists on posts made via the Direct Post API in a 24-hour window; the docs give no number. The error is HTTP 403 `spam_risk_too_many_posts` (official pages, via search extract) — [TikTok Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines); [TikTok Direct Post reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- Third-party guides put that cap at "around 15 posts per creator per day". UNCONFIRMED against TikTok's docs (vendor/secondary) — [Blotato](https://www.blotato.com/api/tiktok); [upload-post](https://www.upload-post.com/tiktok-api/)
- Two modes: Direct Post publishes to the profile; Upload mode sends the video to the creator's TikTok inbox as a draft that must be completed manually in the mobile app (anecdote, consistent across the thread) — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Audit UX requirements seen in actual rejections: "Users must manually select the privacy status from a dropdown and there should be no default value"; comment/duet/stitch off by default; a creator-info card shown before posting (anecdote, quoting TikTok's rejection email dated May 2026) — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Personal-use ineligibility: on 30 October 2026 a nonprofit user reported TikTok support replies saying that an application "intended to publish only to your organization's own single TikTok account" is personal or internal use and not eligible; that user abandoned the audit and posts manually (anecdote) — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Vendor guides say the same: apps that look like internal tools, side projects or demos are rejected, and a one-click publish with no review screen fails (vendor/secondary) — [bundle.social](https://bundle.social/blog/tiktok-api-approval); [postwire](https://postwire.io/platforms/tiktok/approval/)
- Review time: TikTok's App Review FAQ is quoted as "several days to two weeks after submission"; vendor guides say 2-6 weeks and that requested changes reset the clock; one self-hoster in the Postiz thread reported approval on 7 September 2026 after roughly four months of resubmissions, while others were rejected repeatedly without specific reasons (mixed: vendor/secondary and anecdote) — [adaptlypost](https://adaptlypost.com/blog/why-does-my-app-need-approval-post-tiktok); [postproxy](https://postproxy.dev/blog/how-to-post-to-tiktok-via-api/); [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Reported alternative: on 2 November 2026 a user said they published public videos through the TikTok API for Business (Accounts API) after an access form approved in two days; this route has no privacy field, so videos take the account default (single anecdote, UNCONFIRMED) — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)

### Inferences
- "Typical review time" is best stated as a range: TikTok says days to two weeks; practitioners report 2-6 weeks; self-hosted single-user applicants report months or outright ineligibility.
- A solo operator with one or a few own accounts does not fit TikTok's audit criteria. Full hands-off public posting to TikTok therefore depends on a third-party service whose app is already audited, and the per-creator daily cap still applies through that service.
- Inbox/draft upload is the compliant semi-automatic fallback, with one manual tap-through per video on a phone.

### Gaps
- The official numeric per-creator daily cap is not published; the "about 15" figure is vendor-sourced.
- TikTok's own written eligibility text excluding personal/internal tools was not retrieved; the evidence is support replies quoted by applicants.
- Whether the Accounts API (TikTok API for Business) route is a sanctioned path for organic clip posting by an individual is unverified.

## 3. Instagram content publishing (Reels) and Facebook Reels API

### Takeaway
Instagram is the most permissive of the four for a solo operator: an app that only serves the developer's own professional account can run on Standard Access without App Review, with a limit of 100 API-published posts per 24 hours (some Meta pages still say 50). Serving other people's accounts needs Advanced Access, which requires App Review plus Business Verification.

### Cited Findings
- Instagram accounts are limited to 100 API-published posts within a 24-hour moving period; carousels count as one; enforced at `POST /<IG_ID>/media_publish`; usage is readable at `GET /<IG_ID>/content_publishing_limit` (official page, via search extract) — [Meta: Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- Documentation inconsistency: the same page's carousel section and the `content_publishing_limit` reference still say 50, and a developer blog reports a live `quota_total` of 50 (vendor/secondary) — [bundle.social](https://bundle.social/blog/instagram-api-rate-limits); [postproxy](https://postproxy.dev/blog/instagram-reels-api-publishing-guide/)
- An Instagram account can create at most 400 media containers in a rolling 24 hours; an unpublished container expires after 24 hours; video is processed asynchronously and the container must reach `FINISHED` before publishing (official page, via search extract) — [Meta: IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media)
- Account type: publishing requires a professional account. One guide says Reels publishing works only with Business accounts and not Creator accounts; Ayrshare reports Instagram added Creator-account publishing. CONFLICT, not resolved against Meta's page — [postproxy](https://postproxy.dev/blog/instagram-reels-api-publishing-guide/); [Ayrshare](https://www.ayrshare.com/blog/the-instagram-api-just-went-to-11.md)
- If the app only serves the developer's own Instagram professional account or an account they manage, Standard Access is all the app needs (official page, via search extract) — [Meta: Create an Instagram app](https://developers.facebook.com/documentation/instagram-platform/create-an-instagram-app)
- Advanced Access requires App Review and Business Verification, and the app must be connected to a verified business before it can be published (official pages, via search extract) — [Meta: Access Verification](https://developers.facebook.com/documentation/development/release/access-verification); [Meta: Permissions Reference](https://developers.facebook.com/docs/permissions)
- Timing: no official Meta figure. A vendor guide says standard permissions commonly clear App Review in about 2-4 weeks; a Meta community forum post from April 2026 describes Business Verification stuck "In Review" for more than 10 days, blocking App Review submission (vendor/secondary and anecdote) — [Meta community forum](https://communityforums.atmeta.com/questions-and-discussions-26/business-verification-in-review-10-days-blocking-app-review-submission-177498); [qpost.dev](https://qpost.dev/guides/publishing-api-access/)

### Inferences
- For the operator's own accounts, Instagram Reels publishing can be scripted without any review queue; the practical limits are the 50-100 posts/day cap (far above any sensible posting rate) and the need for a publicly reachable video URL or resumable upload.
- The API cap is not the constraint; recommendation eligibility for reposted clips is (see section 7).

### Gaps
- Facebook Reels API (Page Reels publishing): no official limit was retrieved. A "30 Reels per Page per 24 hours" figure circulates but was not confirmed by any source in this session.
- Whether Creator (not Business) accounts can publish Reels via the API in November 2026 is unresolved.
- Reels-specific spec limits (max duration, file size) from Meta's page were not retrieved.

## 4. X API posting tiers and video cost

### Takeaway
New X developers in 2026 are on pay-per-use pricing with no subscription; a post costs about $0.015 and a post containing a URL about $0.20, so native video posts without links are cheap at clip-channel volumes. All price figures here come from secondary trackers because docs.x.com could not be read directly.

### Cited Findings
- X's API is pay-per-use for new developers; legacy subscription tiers are grandfathered for existing subscribers and new projects can only use pay-per-usage (vendor/secondary) — [postproxy](https://postproxy.dev/blog/x-api-pricing-2026); [socialync](https://www.socialync.io/blog/x-api-pricing-and-posting-2026)
- Creating a post is listed at $0.015; a post containing a URL at $0.20 (vendor/secondary) — [upload-post X API pricing](https://www.upload-post.com/x-api-pricing/); [opentweet (states "last checked against docs.x.com")](https://opentweet.io/x-api-pricing)
- The media upload endpoints (initialize, append, finalize) appear with rate limits (180,000 per 24 hours; 1,875 per 15 minutes for initialize) and no per-call price in the pricing table (vendor/secondary) — [upload-post X API pricing](https://www.upload-post.com/x-api-pricing/)
- Video upload is a chunked flow: initialize, append, finalize, then poll processing status before attaching to a post (vendor/secondary) — [postmypost help](https://help.postmypost.io/docs/channels/x/issue/10248)
- Sources disagree on the monthly pay-per-use read cap (2 million vs 3 million post reads) and on the post-read price ($0.005 vs $0.01) (vendor/secondary, CONFLICT) — [socialcrawl](https://www.socialcrawl.dev/blog/x-twitter-api-2026); [1322.io](https://1322.io/blog/twitter-api-pricing)

### Inferences
- At $0.015 per post, 10 video posts a day costs roughly $4.50 a month if media upload calls are unbilled. Including a link in each post would raise that to about $60 a month, so links (for example campaign tracking links) are the cost driver.
- X requires no app audit comparable to YouTube's or TikTok's for own-account posting; the gate is payment.

### Gaps
- Official per-call billing of media upload endpoints is unconfirmed.
- Current status and price of legacy Basic/Pro tiers and of any free tier or signup credit (one Japanese-language source mentions a $70 credit from 2 October) could not be confirmed against docs.x.com.
- Whether X Premium is needed for longer or higher-resolution video via API was not researched.

## 5. Third-party schedulers and posting APIs as a substitute for doing one's own audits

### Takeaway
Paying an already-audited intermediary is the standard way round the YouTube private-lock and TikTok audit problems, at roughly $16-$100 a month for a handful of accounts. Platform content, spam and per-account posting limits still apply through these tools.

### Cited Findings
- Blotato: Starter $29/month (20 accounts, 1,250 AI credits), Creator $97/month, Agency $499/month; API included on paid plans, not on the 7-day trial (vendor) — [Blotato pricing research](https://www.blotato.com/research/social-media-agent-pricing); [PlugKit](https://www.plugkit.co/blog/blotato-pricing-api-alternatives/)
- Postiz: cloud from $29/month; open-source self-hosted version is free, but self-hosting means the operator must supply their own platform app credentials and therefore their own audits (vendor plus anecdote) — [Blotato pricing research](https://www.blotato.com/research/social-media-agent-pricing); [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Ayrshare: Premium from $149/month; Business tiers $499-$599/month; about $770/month at 50 profiles (competitor-sourced) — [Blotato: Ayrshare alternatives](https://www.blotato.com/blog/ayrshare-alternatives)
- Metricool: free plan 1 brand and 20 posts/month; API only on Advanced ($75-$77/month depending on source) and Custom (secondary) — [Blotato pricing research](https://www.blotato.com/research/social-media-agent-pricing)
- Publer: about $5 per account per month ($4 annual); API on Business at about $10 per account; sources conflict on which tier includes the API (secondary, CONFLICT) — [dev.to price comparison, October 2026](https://dev.to/perufitlife/9-social-media-posting-apis-compared-by-price-october-2026-read-from-each-pricing-page-509b)
- Repurpose.io: Starter $35/month, Pro $79/month, Agency $179/month; no self-serve API/agent publishing documented (secondary) — [Blotato pricing research](https://www.blotato.com/research/social-media-agent-pricing)
- Upload-Post: $24/month or $16/month billed yearly, with a free plan (competitor/vendor-sourced) — [upload-post](https://www.upload-post.com/blotato-alternative/)
- Buffer publishes its own overview of multi-platform posting APIs (not read in full) — [Buffer](https://buffer.com/resources/social-media-api-multi-platform-posting/)
- Per-platform caps pass through: Upload-Post exposes an endpoint to read the Instagram publishing limit, and TikTok's per-creator cap is enforced on the creator account regardless of the client (vendor docs and official page via extract) — [Upload-Post docs](https://docs.upload-post.com/api/get-instagram-publishing-limit); [TikTok Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)
- On reach: Instagram head Adam Mosseri is reported (second-hand) to have said scheduling tools do not affect reach; Buffer's and Hootsuite's own tests found no penalty but used very small samples; no official TikTok or YouTube statement on scheduler reach was found (vendor/secondary) — [Buffer](https://buffer.com/resources/scheduled-posts/); [Hootsuite](https://blog.hootsuite.com/experiment-third-party-scheduling-instagram/); [Metricool](https://metricool.com/does-scheduling-social-media-posts-affect-reach/)

### Inferences
- Using a scheduler is within platform terms in the sense that these services publish through the official APIs under apps that have passed each platform's review; that is the purpose of the review regimes. No source showed a platform prohibiting use of audited third-party publishers.
- A scheduler does not launder content-policy risk: reposted, mass-produced or duplicated clips are judged the same however they were uploaded.
- Budget planning figure for a solo operator: about $16-$30 a month for a low tier covering YouTube, TikTok, Instagram and X, more if the vendor surcharges X posts because of X's per-post fees.

### Gaps
- No clean, comparable "posts per month" or "posts per day per platform" table across vendors was found; limits are stated in accounts, profiles or credits. Vendor pricing pages should be read directly.
- Current Buffer and Later prices and API availability were not retrieved.
- Most comparisons were written by competing vendors (Blotato, Upload-Post), so rankings are unreliable; prices are probably accurate but should be re-verified.
- No vendor's own statement on whether TikTok posts via their API are public by default (direct post) or inbox drafts was confirmed per vendor.

## 6. Downloading source video: ToS position, official Twitch/Kick routes, yt-dlp reliability

### Takeaway
Scripted downloading from YouTube breaches YouTube's Terms of Service and is unreliable from datacenter IPs in 2026. The sanctioned automated source is Twitch's API (clip creation, clip-from-VOD in beta, and an authorised clip download endpoint that needs the broadcaster's or an editor's token), or files handed over by the creator or campaign.

### Cited Findings
- YouTube's Terms prohibit accessing, reproducing, downloading or otherwise using content except as expressly authorised by the Service or with prior written permission from YouTube and rights holders, and prohibit accessing the Service by automated means (robots, botnets, scrapers) except for public search engines following robots.txt or with YouTube's written permission (official text as captured in search extract and archived copy; live page not read) — [YouTube Terms of Service](https://www.youtube.com/terms); [archived 2021 copy](https://web-wp.archive.org/web/20210416000515/https:/www.youtube.com/t/terms)
- yt-dlp's PO Token Guide (last edited 12 July 2026) says YouTube is rolling out proof-of-origin token enforcement across most clients, that missing tokens can cause HTTP 403 errors or lead to the account or IP being blocked, and that tokens are now bound to individual video IDs so manual extraction is no longer recommended — [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- Requests from AWS, GCP, Azure and other major datacenter ranges increasingly return HTTP 403 or the "Sign in to confirm you're not a bot" challenge; the same command that works from a residential connection fails from a cloud server (vendor/secondary and developer write-ups) — [stormkeep](https://stormkeep.pages.dev/blog/yt-dlp-at-scale-2026); [dev.to write-up](https://dev.to/osovsky/i-was-building-a-cloud-video-service-youtube-turned-me-into-an-ip-trafficker-1l9o)
- A widespread breakage is reported on 17 August 2026 (403 errors), with yt-dlp removing an affected client from defaults on 18 August and shipping a fix in stable 2026.08.19 (secondary, UNCONFIRMED against the yt-dlp changelog) — [VideoProc](https://www.videoproc.com/resource/yt-dlp-not-working.htm)
- Twitch Create Clip: requires a user token with `clips:edit`, the broadcaster must be live, and it captures up to 90 seconds; creation is confirmed by polling Get Clips (official docs via search extract) — [Twitch API: Clips](https://dev.twitch.tv/docs/api/clips/)
- Twitch "Create Clip From VOD" entered open beta in December 2025 and can also clip from earlier in a current stream if VODs are enabled — [Twitch developer forum](https://discuss.dev.twitch.com/t/introducing-clip-api-improvements-and-clip-from-vod-in-open-beta/64492); [Twitch change log](https://dev.twitch.tv/docs/change-log/)
- Twitch "Get Clips Download" lets developers download clip video files on behalf of an authenticated broadcaster or editor of the channel, using `editor:manage:clips` or `channel:manage:clips`; the beta announcement gave a custom rate limit of 100 requests per minute and up to 10 clip IDs per request; the change log later marked it generally available — [Twitch developer forum](https://discuss.dev.twitch.com/t/get-clips-download-twitch-api-endpoint-now-available-in-open-beta/64334); [Twitch change log](https://dev.twitch.tv/docs/change-log/)
- There is still no official API to download or export full Twitch VODs (developer forum request thread) — [Twitch developer forum](https://discuss.dev.twitch.com/t/official-api-endpoint-to-download-or-export-twitch-vods-similar-to-the-clips-download-endpoint/64561)
- Kick: a public API launched in 2024 using OAuth 2.1 with a registered application (vendor/secondary); no source confirmed official clip or VOD download endpoints. One blog says Kick's Terms (section 1.4) ban "robots", "spiders" and "offline readers"; another says the Terms permit clipping for personal and transformative use; a third says Kick added a download button for a streamer's own VODs on 25 September 2026 (single source). All UNCONFIRMED — [autoclip.dev](https://autoclip.dev/blog/kick-clipping-guide-2026); [upstream.so](https://upstream.so/blog/how-to-download-kick-vods/); [streamer.guide](https://streamer.guide/guides/how-to-download-kick-streams-clips)

### Inferences
- The only fully sanctioned, scriptable source of footage is one where the rights holder is in the loop: a streamer who grants the operator editor status on Twitch (enabling API clip creation and download), or a campaign that distributes source files or links. Without that, automated acquisition means breaching a platform's ToS and depending on tooling that broke at least once in 2026 for all users.
- For game trailers and publisher footage (for example GTA 6 material on YouTube), there is no official download API at all; acquisition would rest on the publisher's or campaign's own asset distribution.
- A server-side yt-dlp step should be treated as a fragile, non-compliant dependency rather than infrastructure. This note deliberately does not list workarounds.

### Gaps
- Twitch's Terms of Service wording on downloading other users' VODs was not retrieved.
- Kick's actual Terms and developer documentation were not read; all Kick statements are from third-party blogs that contradict each other.
- No measured failure rate for yt-dlp from datacenter IPs was found; evidence is qualitative.

## 7. Platform rules on automation, bots, multiple accounts and reach suppression

### Takeaway
Every platform allows official-API posting but explicitly prohibits automated account creation, running many accounts that post the same or near-identical content, and evading bans with new accounts. Separately, each has an originality regime under which reposted or templated clips lose recommendation or monetisation even when no rule is "broken". These originality regimes are the main documented reach risk; no platform documents a penalty for API posting as such.

### Cited Findings
- YouTube renamed its "repetitious content" monetisation policy to "inauthentic content" on 15 July 2025; a July 2026 clarification describes generic or repetitive content as "content that looks like it's made with a template, or that may feel repetitive to viewers after watching several videos in a row from the same channel", and YouTube frames it as a refinement, not a new rule — [Tubefilter, 13 July 2026](https://www.tubefilter.com/2026/07/13/youtube-inauthentic-content-monetization-policy-update/); [Metricool](https://metricool.com/youtube-ai-content-rules-monetization/)
- In January 2026 YouTube reportedly terminated 16 channels with a combined 35 million subscribers and 4.7 billion views under the inauthentic content policy (single source, UNCONFIRMED) — [The Next Web, 15 June 2026](https://thenextweb.com/news/youtube-ai-slop-crackdown-faceless-creators-collateral-damage?type=AI)
- Creators report automated systems flagging original content as inauthentic, and YouTube has not defined the threshold for "too much" repetitive content — [The Next Web](https://thenextweb.com/news/youtube-ai-slop-crackdown-faceless-creators-collateral-damage?type=AI); [TheDesk](https://thedesk.net/2026/07/youtube-clamps-down-low-quality-ai-slop/)
- YouTube circumvention: violations may result in termination of all existing channels and any new channels the user creates or acquires; re-posting content from terminated creators counts as circumvention — [YouTube Help](https://support.google.com/youtube/answer/2801981?hl=en-GB); [YouTube policy updates](https://support.google.com/youtube/answer/10008196?hl=en-US)
- On 29 October 2026 YouTube announced a test letting channels facing termination for certain spam violations fix or remove the content during a 30-day locked period instead of immediate termination; eligibility is not detailed — [Android Authority](https://androidauthority.com/youtube-channel-termination-video-deletion-policy-3730133); [Dexerto](https://www.dexerto.com/youtube/youtube-tests-new-policy-letting-creators-delete-videos-to-avoid-permanent-bans-3423181/)
- TikTok: "We strictly prohibit automation tools, scripts, or other tricks designed to bypass our systems"; spam includes "using automation to run many accounts or send repetitive content"; multiple accounts are allowed but not to deceive or break rules; a banned or restricted user may not open another account, and TikTok may ban new accounts they create — [TikTok Creator Academy: Integrity and Authenticity](https://www.tiktok.com/creator-academy/article/integrity-authenticity-on-tiktok); [TikTok Community Guidelines](https://www.tiktok.com/community-guidelines)
- The September 2026 TikTok guidelines update is reported to add that TikTok may ban "associated accounts", a term the guidelines do not define (secondary, UNCONFIRMED) — [socialscalehub](https://www.socialscalehub.com/academy/tiktok-community-guidelines-september-24-2026)
- TikTok originality: reused material posted without creative edits, including clips showing another platform's watermark or logo, is ineligible for the For You feed; content ineligible for the For You feed is not eligible for the Creator Rewards Program; accounts that keep posting ineligible content may become harder to find. The video stays on the profile — [TikTok Creator Academy: Originality policy](https://www.tiktok.com/creator-academy/article/tiktok-originality-policy); [shapelessai summary](https://shapelessai.com/notes/unoriginal-content-rules-youtube-tiktok-instagram)
- Instagram: since April 2024, when identical copies exist Instagram recommends only the original and labels the copy; accounts that repeatedly repost others' content lose recommendation eligibility and regain it 30 days after their last unoriginal post; there is no strike or removal attached (secondary) — [tryclipup](https://www.tryclipup.com/instagram-unoriginal-content-policy); [shapelessai summary](https://shapelessai.com/notes/unoriginal-content-rules-youtube-tiktok-instagram)
- The commonly quoted Instagram "10 or more reposts in 30 days" trigger could not be confirmed in any source retrieved in this session; only the 30-day recovery window was consistently reported — [post-bridge](https://www.post-bridge.com/blog/does-cross-posting-hurt-engagement)
- Instagram/Meta terms are summarised by secondary sources as forbidding account creation by unauthorised or automated means and creating a new account after one is disabled (secondary, not checked against Meta's text) — [viralday](https://viralday.app/en/blog/new-tiktok-and-instagram-accounts-a-no-myths-checklist); [DeviceFarm](https://devicefarm.io/blog/how-many-accounts-per-phone)
- X (automation rules page marked "Updated April 2026"): creating or automating several accounts for duplicative or near-identical purposes is prohibited; the authenticity policy lists operating multiple accounts that post substantially similar or identical content, including cross-posting; automated posting about trending topics is prohibited; enforcement includes filtering from search, suspension and loss of API access — [X Help: Automation rules](https://help.x.com/en/rules-and-policies/x-automation); [X Help: Authenticity](https://help.x.com/en/rules-and-policies/authenticity)
- Device/IP linking: no platform publishes how it links accounts. TikTok publishes no per-phone account limit; "account linking" descriptions come from vendor blogs (secondary) — [DeviceFarm](https://devicefarm.io/blog/how-many-accounts-per-phone); [socialscalehub](https://www.socialscalehub.com/academy/tiktok-account-linking-detection-in-2026-how-it-works-how)
- "Warm-up": no official documentation of a warm-up period was found. The only related claim is a vendor's statement that a new TikTok account which immediately connects a third-party tool "looks like a bot" (vendor claim, not a TikTok statement) — [Blotato](https://www.blotato.com/blog/does-api-posting-hurt-reach)
- API-posted reach: no official penalty is documented on any platform; Instagram's API documentation mentions none; evidence against a penalty is small-sample and vendor-produced — [Hootsuite](https://blog.hootsuite.com/experiment-third-party-scheduling-instagram/); [poster.ly](https://www.poster.ly/blog/does-scheduling-hurt-reach-research)

### Inferences
- The documented, enforced risks for an automated clip channel are, in order: (1) originality/inauthentic-content demotion or demonetisation of unedited or templated reposts; (2) spam enforcement for the same clip posted across several of the operator's accounts; (3) termination spreading to linked or future accounts after a ban. "API posting is shadowbanned" and "accounts must be warmed up" are community beliefs with no official support found.
- Because a ban can extend to "associated" or future accounts and the linking method is undisclosed, a multi-account strategy concentrates risk rather than spreading it.
- A fully automated pipeline that produces near-identical captioned gameplay clips fits YouTube's wording for "generic or repetitive" content closely; this is a monetisation and termination risk independent of any upload-API issue.

### Gaps
- The exact current text of Instagram's recommendation guidelines on aggregators (including any numeric repost threshold) was not retrieved.
- Official statements on device or IP fingerprint linking do not exist publicly; nothing can be confirmed.
- No official YouTube Shorts-specific statement on reach for reused clips (as distinct from monetisation) was retrieved.
- X's treatment of API-posted video in ranking, and any requirement to label automated accounts, was not confirmed from the Help Center.

## 8. State of auto-clipping tools in 2026 and highlight detection for gaming streams

### Takeaway
Commercial clippers cost roughly $15-$40 a month and are built mainly for talking-head content; Eklipse is the gaming-specific one and became paid-only in June 2026. No independent accuracy benchmark on gaming footage was found. Open-source pipelines converge on the same design: chat velocity and audio energy scored against a rolling baseline, then Whisper and an LLM or vision model on candidate windows only.

### Cited Findings
- OpusClip: Starter $15/month (150 minutes of video), Pro $29/month or $14.50/month billed yearly (300 minutes) (secondary) — [stork.ai](https://www.stork.ai/blog/best-ai-video-clippers-2026); [useaifix](https://ainews.useaifix.com/compare/opus-clip-alternatives-ai-clipping-tools/)
- Vizard: free tier 60 credits/month with watermark at 720p; Creator $29/month ($14.50 yearly) for about 600 minutes; Business $39/month ($19.50 yearly) (secondary) — [stork.ai](https://www.stork.ai/blog/best-ai-video-clippers-2026)
- Klap: no real free plan; Basic $29/month (about 100 clips), Pro $79/month, Pro+ $189/month; priced per clip (secondary) — [useaifix](https://ainews.useaifix.com/compare/opus-clip-alternatives-ai-clipping-tools/)
- Submagic: Starter $19/month ($12 yearly, about 15 videos), Pro $39/month ($23 yearly, about 40), Business $69/month (about 100); long-form clipping is an add-on at about $19/month (secondary) — [stork.ai](https://www.stork.ai/blog/best-ai-video-clippers-2026)
- Eklipse: Premium $24.99/month ($179.99/year), Premium Pro $39.99/month; became a fully paid product in June 2026 according to Eklipse itself; clip limit not published; aimed at Twitch, Kick and YouTube gaming streamers (secondary) — [cut.pro](https://cut.pro/en/blog/best-free-clipping-tools-2026); [nexusclips](https://nexusclips.com/blog/best-ai-clipping-tools-2026)
- OpusClip publishes its own overview of clipping APIs (vendor; not read) — [OpusClip](https://www.opus.pro/blog/best-video-clipping-apis)
- Open-source design 1 (TWITCH-Generator): Whisper transcript, chat-replay velocity, loudness bursts, and a two-tier vision pass, fused by weighted sum with each signal scored relative to a rolling baseline of about plus or minus 2.5 minutes — [GitHub: cccdemon/TWITCH-Generator](https://github.com/cccdemon/TWITCH-Generator)
- Open-source design 2 (reactorlab): streamlink plus ffmpeg capture; audio RMS and chat velocity each z-scored against their own recent baseline; non-max suppression; 9:16 crop; chat read anonymously over IRC — [GitHub: windycityassassin/reactorlab](https://github.com/windycityassassin/reactorlab)
- Open-source design 3 (HyperClip-AI): chat-velocity z-score threshold of 2.0 plus emote density, with a multimodal judge using OpenCV frame motion — [GitHub: Deeptanil/HyperClip-AI](https://github.com/Deeptanil/HyperClip-AI)
- Open-source design 4 (autoclipnew): chat, replay heatmaps, ffmpeg audio envelope, then targeted Whisper transcription on candidate windows — [GitHub: PIGJET/autoclipnew](https://github.com/PIGJET/autoclipnew)
- Vision route (Auto-clipper): YOLO object detection, pixel analysis of muzzle flash, damage vignettes and HUD zones, and Whisper voice triggers such as "clip it"; no kill-feed OCR — [GitHub: bendawg2010/Auto-clipper](https://github.com/bendawg2010/Auto-clipper)
- Academic reference: Stanford CS231n 2024 project HilAIt combined Whisper transcription, chat OCR, facial analysis and sentiment — [HilAIt paper](https://cs231n.stanford.edu/2024/papers/hilait-automatic-video-highlighting-system-leveraging-audio-text.pdf)
- None of these projects or reviews reports precision, recall or any gaming-footage accuracy figure — [reap.video report](https://reap.video/reports/state-of-top-ai-video-clipping-tools-2026); [GitHub topic: highlight-detection](https://github.com/topics/highlight-detection)

### Inferences
- Signal ranking for gaming streams, from how the open-source projects are built: chat velocity (cheap and strong on live streams with an active chat) first; audio energy normalised to a local baseline second (raw loudness is weak in constantly loud games); transcript plus LLM for context and titles third; game-specific vision (HUD, kill feed) most accurate per title but needs per-game work.
- Chat and audio signals do not exist for trailers or publisher footage (no chat, mastered audio), so for GTA 6 trailer-style source material the transcript/vision route or human selection is what remains.
- A self-hosted Whisper + LLM + ffmpeg pipeline costs compute and LLM tokens rather than a subscription, but a human pass on clip choice and titles is still the obvious quality control given the originality rules in section 7.

### Gaps
- StreamLadder pricing and limits were not retrieved.
- No independent accuracy or "usable clip rate" benchmark for any tool on gaming footage was found.
- Commercial tools' API availability and per-minute API pricing (needed for server-side automation, as opposed to web UI use) was not confirmed.

## 9. What cannot be automated in practice and must remain human

### Takeaway
Account creation and verification, platform app audits, business/identity verification, campaign approval and payout review, and appeals all involve a human gate by design, and automating account creation is itself a stated violation on TikTok, Instagram and X.

### Cited Findings
- YouTube channel verification is by SMS or automated voice call, and higher upload limits come from advanced features obtained through channel history or identity verification (secondary summary of YouTube Help) — [quso.ai](https://quso.ai/blog/youtube-uploading-limits.html); [adaptlypost](https://adaptlypost.com/blog/youtube-upload-limit-per-day)
- YouTube's API audit is a form reviewed by the API Services team, with a separate appeals form (official, via search extract) — [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits?hl=en)
- TikTok's audit requires a recorded demo of the full consent and posting flow and is reviewed by staff, with rejections delivered by email (anecdote plus vendor) — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563); [bundle.social](https://bundle.social/blog/tiktok-api-approval)
- TikTok inbox/draft mode requires the user to finish each post in the mobile app — [Postiz issue #1563](https://github.com/gitroomhq/postiz-app/issues/1563)
- Meta Advanced Access requires Business Verification and App Review (official, via search extract) — [Meta: Access Verification](https://developers.facebook.com/documentation/development/release/access-verification)
- Automated account creation is prohibited: TikTok lists using automation to run many accounts as spam; Instagram's rules are summarised as forbidding automated account creation; X prohibits creating or automating multiple accounts for duplicative purposes — [TikTok Creator Academy](https://www.tiktok.com/creator-academy/article/integrity-authenticity-on-tiktok); [viralday](https://viralday.app/en/blog/new-tiktok-and-instagram-accounts-a-no-myths-checklist); [X Help: Automation rules](https://help.x.com/en/rules-and-policies/x-automation)
- Paid clipping campaigns (Content Rewards on Whop): each campaign owner has review authority; Whop's docs are described as providing brand approval of every submission with a 48-hour auto-approve, per-video payout caps and minimum thresholds; payment is subject to verification and the campaign owner's review (vendor/secondary) — [Content Rewards guide](https://contentrewards.com/blog/mega-clipping-guide-content-rewards); [ClipSpeedAI](https://www.clipspeed.ai/blog/whop-content-rewards-clipping-guide.html)
- Whop's terms allow fund holds and reserves pending verification; bot-detection flags near payout are a recurring source of disputes; buying or generating non-genuine engagement is a permanent ban; standard payouts take up to 5 business days (secondary) — [clipaffiliates](https://www.clipaffiliates.com/blog/is-whop-content-rewards-legit); [OpenClip Whop guide](https://openclip.app/guides/whop-clipping-guide)
- Fee figures conflict: about 7% payout fee in one source, a flat 10% creator fee in another (secondary, CONFLICT) — [ClipSpeedAI](https://www.clipspeed.ai/blog/whop-content-rewards-clipping-guide.html); [clipaffiliates](https://www.clipaffiliates.com/blog/is-whop-content-rewards-legit)
- Clips submitted to Content Rewards since 25 September 2026 earn on views for 30 days from the day they cross the payout threshold (vendor) — [Content Rewards guide](https://contentrewards.com/blog/mega-clipping-guide-content-rewards)

### Inferences
- Steps that can be scripted by a solo operator within the rules: clip detection and rendering; captioning; uploading to Instagram (own app, Standard Access), X (pay-per-use), and YouTube/TikTok through an audited third-party service; pulling analytics.
- Steps that stay manual: creating and phone/identity-verifying each account; passing or maintaining any platform app audit; Meta business verification; joining a campaign, submitting each clip link (unless the marketplace offers an API, which was not found), and responding to rejections; payout onboarding; and every appeal after a strike, demonetisation, hold or termination.
- Because campaign payouts depend on verified genuine views and a human or semi-automated review, the revenue step is the least automatable and the most exposed to holds.

### Gaps
- No source confirmed KYC or tax-form requirements (for example W-9/W-8BEN, 1099 handling) for Whop/Content Rewards payouts; these are standard for payout platforms but were not documented in anything retrieved.
- Whether any clipping marketplace offers a submission API was not found.
- Appeal turnaround times on any platform were not retrieved.
