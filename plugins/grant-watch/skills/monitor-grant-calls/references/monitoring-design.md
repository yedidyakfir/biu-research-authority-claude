# Grant monitoring design

## Seed evidence and boundaries

The initial workbook has 41 dated call rows from 2019–2025, 39 distinct funder URLs, 27 BIU URLs, and no opening or publication dates. Two old rounds reuse a durable funder URL. These observations justify separate program and round records. Keep the workbook and operational database outside this public repository.

This is a design contract for a future scheduled service. Installing a skill does not create a recurring job or authorize university-wide publication.

## Durable state

Use SQLite on a single persistent pilot host, with transactional backups; use a shared transactional database if multiple workers or hosts are later required. Minimum logical records:

| Record | Essential fields |
| --- | --- |
| `program` | stable ID, funder, title/aliases, active/retired/unknown, expected cycle window if evidenced, owner, coverage tier |
| `source` | program ID, canonical URL, source type, access method, reliability, check cadence, next due, last success, ETag/Last-Modified, relevant-content hash, final URL, failure count |
| `round` | program ID, call ID or cycle, stage, announcement/open/close timestamps with source timezone, status, source evidence, verified time |
| `observation` | source ID, fetch time, HTTP/result type, content fingerprint, detected links/fields, raw evidence reference, parser version |
| `event/outbox` | round ID, event type, milestone/version, audience, created time, review/delivery state, unique deduplication key |
| `run` | started/finished time, checks due/completed/failed, last successful calendar and discovery passes, heartbeat |

Store only short evidence and source metadata long term. Put temporary downloaded pages in a bounded private cache. Do not commit grant state, credentials, recipient lists, or unreviewed drafts to the plugin repository.

## Scheduling and cost

- Run once daily at an off-hour minute in `Asia/Jerusalem`, for example 07:17, with a lock so runs do not overlap. Run the date calculator and digest every day. Check for a missed or incomplete run separately, and backfill calendar events after downtime.
- Poll official APIs/feeds and registered announcement sources daily. For the current 39 distinct funder URLs, a daily lightweight conditional GET or relevant-section hash check is practical **where the host permits it**. These are network checks, not 39 model research tasks. Stagger requests by host; a blocked source gets a slower approved path and an explicit coverage warning.
- Research only new records, relevant-content changes, uncertain extractions, and failed source transitions. Recheck active calls and their linked official documents around opening and deadline milestones. Run a monthly independent discovery audit to find moved pages and programs outside the seed list.
- Conditional GET can return `304` without a body. Where validators are missing, hash the meaningful page section; layout or navigation changes should not trigger date extraction. A hash alone proves only that the extracted section is unchanged, so periodically audit parser coverage.
- Generate the routine digest from database facts and templates. Spend model tokens on the small escalated set; meter checks, changed pages, model invocations, and tokens per run. No fixed token budget can be promised before observing source behavior.

## Decision gates

1. **Discovery:** candidate call or changed page is seen in an official API/feed, BIU announcement, or registered source.
2. **Verification:** resolve to an official funder call page or document; extract stage, publication/open/close timestamps and timezone. Record uncertainty where a field is absent. Another announcement may support, but should not silently override, the funder.
3. **Reconciliation:** compare stable call identity and stage with the prior round. A new URL alone is not a new grant; an old URL may host a new round. A deadline revision creates a versioned event with old and new dates.
4. **Notification:** administrator digest each day. A university-wide draft is eligible only after verification and policy review; use event keys to prevent repeats. Deadline reminders are generated from stored dates, even with an unchanged source page.
5. **Failure:** HTTP errors, login walls, CAPTCHAs returned with status 200, malformed feeds, stale checks, and missed jobs create visible failures. Never convert them to a 'no change' result.

Suggested default reminder milestones are 30, 14, 7, and 1 days before each verified submission deadline; configure them with the Research Authority. For very short calls, send only milestones still in the future. Always include the exact stage, local date/time, official URL, and any internal BIU deadline separately.

## Reliability limits

The operational target is a daily digest plus a measurable source coverage report, not an unqualified guarantee of every call. A source checked every day can normally be detected within one completed daily run **if** it publishes the call in accessible monitored content and the parser recognizes it. Otherwise detection is bounded by the independent audit cadence or a manual check. Track these conditions per funder.

Prefer official APIs when available, such as the [EU Funding & Tenders APIs](https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis) and [Grants.gov search API](https://www.grants.gov/api/common/search2). [Conditional HTTP requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Conditional_requests) reduce transfer on unchanged sources. [Sitemaps and RSS](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) aid discovery but may omit older items or have inaccurate timestamps. BIU's [public announcements](https://research.biu.ac.il/he/allnews) and [RSS](https://research.biu.ac.il/he/newsrss) are secondary signals; a live probe returned a CAPTCHA HTML page with HTTP 200 on a repeat feed request. The [calls page](https://research.biu.ac.il/calls-for-proposals) requires login. A [ChatGPT Scheduled task](https://developers.openai.com/training/walkthroughs/scheduled-tasks) can invoke the installed skill daily, but local files require the computer and desktop app to be available. If GitHub Actions is chosen as a scheduler, its [scheduled runs can be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows); add a heartbeat, manual backfill, and [concurrency control](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency). A persistent host with durable state is simpler for the pilot.

## Pilot probe, 10 November 2026

One request per sample source showed that the Progeria Research Foundation page returned HTML without ETag or Last-Modified; the PKD Foundation URL redirected to a new path and returned Last-Modified but no ETag; the historical NIH URL returned HTTP 403 to the probe. The probe did not establish whether any of these programs has a current call. It demonstrates why source-specific strategies, redirects, and failure reporting are necessary.
