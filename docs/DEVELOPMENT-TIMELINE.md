# Development Timeline and Provenance

This note separates the assignment schedule from the repository's recorded development history. It was added on 2026-11-02; it does not rewrite earlier commit metadata.

## Requested assignment timeline

The team supplied these requested development milestones:

| Requested milestone (Asia/Kolkata) | Planned Jira sprint window |
|---|---|
| 2026-10-20 10:04 IST | Week 2: 2026-10-20 10:00 to 2026-10-27 10:00 IST |
| 2026-10-24 14:37 IST | Week 2: 2026-10-20 10:00 to 2026-10-27 10:00 IST |
| 2026-10-28 11:15 IST | Week 3: 2026-10-27 10:00 to 2026-11-03 10:00 IST |

These timestamps describe the requested assignment timeline. The available project records do not independently establish that specific source changes occurred at those times.

## Verified Git timestamps

The feature branch records the following author and committer timestamps (Asia/Kolkata):

| Commit | Message | Author date | Committer date |
|---|---|---|---|
| `6fcc02e` | `feat: define medicine booking domain API` | 2026-11-02 21:14:17 IST | 2026-11-02 21:14:17 IST |
| `7fc3784` | `feat: implement booking domain and allocation policies` | 2026-11-02 21:23:23 IST | 2026-11-02 21:23:23 IST |
| `5d1624b` | `test: cover booking priority and allocation safety` | 2026-11-02 21:23:28 IST | 2026-11-02 21:23:28 IST |

The current local source-file metadata records file creation/edits on 2026-11-02 between 20:04 and 20:29 IST. Filesystem timestamps are mutable and are not an independent, tamper-evident record of earlier activity.

## Verification

The five tests in `tests/test_medicine_booking.py` passed in a local run on 2026-11-02. No earlier test-run record was available in the repository. SRS requirement NFR-QA-09 requires functional requirements to be traceable to test cases; the SRS does not prescribe a historical Git commit schedule.

`main` remains unchanged. This branch preserves its existing commits and records the date distinction transparently.
