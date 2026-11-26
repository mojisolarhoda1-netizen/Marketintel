# What the automated tests cover — and what they don't

Run with `python -m pytest -q` (or `run_tests.bat`). Currently **60 tests**, all passing.

Every test runs **offline**: data-source responses are replaced by recorded example files in `tests/fixtures/`. Those files copy the *structure* of real API and feed responses but contain invented content. So the tests prove the app handles responses correctly. They do not prove the real services respond today; `validate.bat` checks that on your laptop.

## Tested

### Data sources (13 tests, `test_engine.py`)
| Test | What it proves |
| --- | --- |
| parse RSS and Atom | Both feed formats are read; dates are converted to UTC; HTML is stripped from summaries |
| keyword matching | "Acme Pay" matches, "Acmeville" doesn't (whole words only) |
| RSS collect, dedupe, score | Matching headlines are saved once; a second run adds 0 duplicates; positive/negative tone is assigned |
| broken feed | One failing feed doesn't stop the others; its error is recorded |
| retries | A network error is retried and the second attempt succeeds |
| YouTube without key | Shows "unavailable — add YOUTUBE_API_KEY" instead of numbers |
| YouTube collect | Subscribers, total views, video count, average likes/comments and engagement rate are calculated correctly; hidden counts are excluded, not guessed; the channel ID is saved; 3 quota units are counted |
| YouTube quota guard | Stops calling once the daily budget is reached and logs "skipped" |
| YouTube discovery | Creator search is counted against the separate 100-per-day search bucket |
| Instagram collect | Followers, post count and recent-post likes/comments are read; posts with hidden likes are excluded from averages |
| Instagram expired token | Reported clearly as a token problem |
| website robots.txt | If a site disallows bots, the page is never fetched |
| website changes | A first snapshot is stored, an unchanged page is skipped, and a price change raises an alert listing the new price |
| TikTok and X | Show the reason they're unavailable |

### Analysis (9 tests)
- 5 sentiment examples, English and Pidgin
- Growth and spike maths
- Manual entry validation: no negative values, no future dates, no duplicates
- CSV import with a bad row
- Authenticity signals say "unknown" when there isn't enough data

### Reports (3 tests)
- Reports use only stored facts; the period and the "What's missing" list are printed; a PDF is produced
- The paid AI engine refuses to run unless deliberately switched on
- Report text can't inject HTML or JavaScript links

### Data integrity (12 tests, `test_data_integrity.py`)
| Test | What it proves |
| --- | --- |
| survives restart | Data is still there after closing the database, re-running migrations and restarting the app |
| history appends | Two collections create two snapshots; nothing is overwritten and videos aren't duplicated |
| origin labels | LIVE / MANUAL / IMPORTED / SAMPLE are assigned correctly |
| CSV labelled IMPORTED | CSV rows are not confused with typed-in numbers |
| no mixing | Real numbers can't be added to sample entries (form or CSV) |
| overview excludes sample | Headline totals count real data only |
| hide sample | The toggle removes sample data from lists and listening |
| sample reports watermarked | Title, narrative, CSV and PDF all say SAMPLE |
| real report clean | No sample content; provenance shows "MANUAL: 2"; growth is +20% exactly |
| YouTube 30-day rule | YouTube rows older than 30 days are deleted; manual rows are kept |
| backup | A backup holds exactly the same data |
| raw export | Full history export contains every snapshot |

### Web app (8 tests, `test_app.py`)
Every page loads; forged form posts are rejected (CSRF); security headers are set; the full flow works (add brand → collect → competitor → manual number → report → PDF/CSV/Claude hand-off); friendly validation messages appear; unavailable engines are refused; CSV upload works; unknown pages return 404.

### New in v0.3 (13 tests, `test_phase3.py`)
| Test | What it proves |
| --- | --- |
| watchlist lifecycle | Create, add (no duplicates), counts by type, duplicate names refused, deleting a brand cleans the list |
| topic collect | Topic keywords match feed stories; a second scan adds no duplicates; tone is scored; tracked brands in the stories are listed |
| topic matching | Hashtags match; "fintech" does not match "Fintechs" |
| partnership detection | @handle and brand name are found in captions; #ad is recognised as a disclosure; "Acmeville" is not matched; no duplicates |
| sample/real isolation | A real creator's post is never linked to a sample brand |
| campaign facts | Baseline = same number of days just before the start; mentions 4 vs 2 = +100%; followers +10%; only on-topic creator posts counted; disclosure gaps counted; PDF and CSV produced |
| campaign validation | End before start and bad dates are refused |
| opportunity rules | Competitor negative coverage and a +25% creator are both flagged |
| opportunities hide sample | Sample data produces no opportunities unless sample is shown |
| schedules | A weekly watchlist schedule writes one PDF + CSV, doesn't repeat within the week, runs again after it |
| schedules refuse sample | Sample entries can't be scheduled |
| ask pack | The Claude Pro pack holds the rules, the question and the facts; missing Ollama gives a clear message |
| new pages | Every new page loads; campaign PDF/CSV download; topic creation and ask form work |

## Not tested (and how to check instead)

| Not covered | Why | How to check |
| --- | --- | --- |
| Real news feeds responding today | Tests are offline by design | `validate.bat` tests every feed live |
| Real YouTube / Instagram responses | Needs your keys | `validate.bat` makes one real call to each (1 YouTube unit) |
| Running on Windows | Built and tested on Linux; the code avoids OS-specific behaviour | `setup.bat` → `run_tests.bat` should show 60 passed |
| Sentiment accuracy on real Nigerian text | 5 examples aren't an accuracy study | Review 50 real mentions after week 1 and mark wrong ones; we tune the word list |
| Authenticity signals against known fake accounts | No labelled dataset | Treat as "look closer", never as proof |
| Ollama report quality | Optional; not installed in the test environment | Try one report if you install Ollama |
| PDF visual layout | Tests check a valid PDF is produced, not how it looks | Open the PDF from validation in `data\exports\` |
| Browser on phones/tablets | Checked by screenshots during development, not automated | Open the app on a phone on the same laptop (not possible remotely by design) |
| Heavy load (hundreds of brands) | Not needed for validation | Revisit before paid customers |
| Partnership detection on real captions | Text matching can miss brands written oddly, or match a common word | Review the "Creators mentioning this brand" list after a week |
| Topic relevance | A keyword like "oil" can match unrelated stories | Use specific keywords (e.g. "fuel price", "petrol scarcity") |
