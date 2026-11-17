# Results page

`web/src/results/` — the collaborators' view of every session: a filterable list with CSV export, a per-student history, and a detail view per session. It lives on the same site at `/#/results`, unlinked from the student UI, and is read-only. The server side (auth, the endpoints, `display_status`) is in [`../backend/README.md`](../backend/README.md#results-api).

## Routes

| Hash | Screen |
|---|---|
| `#/results` | The session list |
| `#/results/<session_id>` | One session (`session_id` URI-encoded) |

`main.tsx` renders `ResultsApp` when `location.hash` is `#/results` or starts with `#/results/`, and `App` otherwise. It subscribes to `hashchange` through `route.ts`'s `useHash` (a `useSyncExternalStore`). It is a hash route because a path route would 404 on a direct load without a custom Amplify Hosting rewrite, and there is no router library for two screens.

## Components

| File | Owns |
|---|---|
| `ResultsApp.tsx` | The login gate, list ↔ detail routing on the hash, the summary list (fetched once per sign-in, kept with its filters across navigation; **Refresh** refetches), and `DetailRoute`, which fetches the item and then its scenario (`fetchScenario`) for the checklist |
| `LoginForm.tsx` | The password form. The server checks the password; the form only shows its error |
| `api.ts` | `login`, `listSessions`, `getSession`, `Unauthorized`, and the token helpers |
| `SessionList.tsx` | The table, filters, sort, the NetID → student history strip, and the CSV button. Controlled by `ListFilters` (`filters.ts`) |
| `SessionDetail.tsx` | The header, `EscalationChart`, `ActionChecklistReport`, `TranscriptReport`, and the JSON download. Pure over `(record, scenario)` |
| `EscalationChart.tsx` | The SVG step chart, pure over `(timeline, turns, max, goal, timeLimit, endT)` |
| `report.ts` | Pure derivations: chart steps, checklist rows, chip labels and tones, reply latencies |
| `format.ts`, `csv.ts`, `badges.tsx`, `download.ts` | `mmss` / `signed` / outcome labels; `toCsv`; the outcome badge and mode icon; the Blob download |
| `types.ts` | The API's shapes plus shallow guards |

## Token handling

`POST /admin/login` returns a 12-hour bearer token. It is kept in `sessionStorage` (`bridge.adminToken`, every access in try/catch), so closing the tab logs out. Every data call sends `Authorization: Bearer <token>`. Any 401 throws `Unauthorized`, and `ResultsApp` then drops the token and shows the login form with an "expired" notice. **Sign out** does the same without the notice.

## List

| Column | From |
|---|---|
| Date | `created_at` (local time; links to the detail view) |
| NetID | `netid`. Clicking it opens that student's history |
| Scenario | `scenario_title` |
| Mode | 🎤 / ⌨️ from `mode` |
| Outcome | `display_status` + `end_reason`: Success, Escalated to max, Timed out, Ended by action, Failed (a `fail` with no reason), Abandoned, Never connected, In progress (`started` / `active`) |
| Agitation | `start_escalation → escalation` |
| Time | `duration_s / time_limit_s`, `—` with no duration |
| Helpful / escalating | `helpful_count / escalating_count` |

**Filters** sit in one row above the table: NetID search (substring), scenario, outcome, and a From/To date range on the local day of `created_at`. **Sorting** works on any column header (`aria-sort` set), newest first by default. Clicking a row opens the session. The table scrolls horizontally inside its container at phone width.

**Student history** is the only aggregate. Clicking a NetID narrows the list to that exact NetID, oldest first (until another sort is picked), and shows a strip with the attempt count and a sparkline of each attempt's final agitation (the latest in the accent colour). It is computed client-side from the summaries.

**Download CSV** exports exactly the rows on screen, in their current order: `Date` (ISO), `NetID`, `Scenario`, `Mode`, `Outcome`, `Start agitation`, `Final agitation`, `Time used (s)`, `Time limit (s)`, `Helpful`, `Escalating`, `Session ID`. The file is RFC 4180 (every value quoted, `"` doubled, CRLF). A cell that starts with `=`, `+`, `-`, `@`, a tab or a CR is prefixed with `'`, so a spreadsheet never evaluates a self-reported NetID as a formula.

## Detail

**Header:** NetID, scenario, outcome badge, date, mode, start → final agitation, time used / limit, and the ending: the recorded `reason` (an END SIMULATION debrief or the game-over reason) or a sentence for the end reason. **Download JSON** saves the raw item.

**Agitation chart.** A step line of `timeline` (2 px, accent) on a 0–max y-axis, with the x-axis in `mm:ss` from 0 to `time_limit_s`. The line runs on to `duration_s`. Dashed reference lines mark the goal and the max. Every timeline point after the start carries a marker:

| Marker | Meaning |
|---|---|
| Green circle | A turn that lowered agitation |
| Red diamond | A turn that raised it |
| Grey ring | An inactivity charge |

The shape is a second channel beside the colour, because the good/bad tokens sit close together under deuteranopia. A legend sits under the chart. Hovering or focusing a marker (each is a tab stop with a 12 px hit target and an `aria-label`) shows a tooltip with the time, the agitation change, the turn's action chips and the student's line. The turn is matched to the step by `t`.

**Action checklist.** Every scenario action, against the recorded turns:

| State | When |
|---|---|
| Earned | A helpful action credited at least once: the first time and the summed points, plus the repeat count |
| Attempted | Detected but never credited (e.g. a zero-point `requires` miss) |
| Missed | Never detected |
| Occurred | An escalating action (positive points, or END SIMULATION): how many times, the first time, the summed points |
| Avoided | An escalating action never detected |

If the scenario config does not load, the checklist says so and the rest of the view still renders.

**Transcript.** Chat bubbles in order, each with `mm:ss` and the speaker. Student turns carry action chips (`Acknowledge autonomy −3`; a repeat reads `Explain risks · repeat 0`) and a ⌨️ marker when `source` is `text`. Patient turns show the reply latency subtly (`+3.6 s`, the gap from the student turn before).

## Style

Only the `index.css` `@theme` tokens are used, so the page reads as the same site. Tone classes come from lookup objects (`Record<…, string>`), never interpolation.
