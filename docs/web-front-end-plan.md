# Survey Coder web front-end plan

Date: 19 November 2026. Status: revised proposal for review.

## First outcome

**Can a survey user upload a spreadsheet, apply an existing codebook and download usable coded results through a web browser?**

Build and test that workflow first. Prove that users can reach the service, that the server can reach the configured model, and that a coding run produces a complete, traceable output. Use synthetic or approved test data for the first demonstration.

This backlog stages the broader lifecycle in [the project plan](survey-coding-automation-project-plan.md). Human coding, adjudication, discovery and controlled validation remain future requirements. They do not need screens or roles in the first release.

## Minimal roles

Start with **one application role: Survey user**. A signed-in user can upload a spreadsheet and codebook, start a run, inspect its status and download their own results. Apply ownership checks to files, runs and downloads from the start. Users have the same capabilities, but cannot access one another's data.

A service maintainer deploys the application, configures its provider credentials and checks service health through deployment tools. This is an operational responsibility, not an additional product role or administration screen. Keep secrets on the server.

| Add only when this requirement arises | Smallest extension |
| --- | --- |
| People need to share datasets or runs | Add workspace membership and owner/member permissions |
| People must code responses independently | Add coding assignments with server-enforced visibility; introduce a coder permission only if assignment access requires it |
| Someone must resolve disagreements | Grant adjudication permission to a designated user; preserve original labels |
| A formal sealed holdout evaluation requires separation of duties | Add a validation-custodian permission and enforce it before importing or evaluating sealed data |
| Staff need to delegate provider or membership management through the UI | Add the specific administration permission required |

Do not create separate project lead, analyst or reviewer roles while their permissions match those of a Survey user. Record the use case and access difference before adding a role. Business accountability and institutional approvals remain organisational responsibilities.

## First-release workflow

Use a single **New coding run** page, followed by a **Run status and results** page. A small **My runs** list lets users return to their work. Defer a full project workspace and its sidebar sections.

1. **Upload spreadsheet.** Accept XLSX initially; let the user select a worksheet and preview its rows. CSV is the next import format if needed.
2. **Select responses.** Choose one response column and supply its question wording. Choose a unique ID column or generate stable row IDs. Preserve original columns and row order.
3. **Upload existing Markdown codebook.** Show parsed code names and a readable preview. Reject empty codebooks, duplicate names and ambiguous output columns. Do not require editing or publication workflows.
4. **Check and run.** Show eligible and blank response counts, codebook name and configured provider/model. Start coding through a server-side job.
5. **Follow progress.** Show queued, running, completed, completed with errors or failed, with counts and useful error messages. The user can close the browser and return to the run.
6. **Download coded CSV.** Include original data, one nullable binary column per code, and a row status. Include a run manifest identifying the input, codebook, prompt, provider/model and settings.

The server uses one configured provider/model for the first release. A model selector, arbitrary endpoints, multi-question setup and codebook discovery can wait for demonstrated demand. The result download is CSV even when the input is XLSX; label that format in the interface.

## Ordered product backlog

Work in the order below. Each item has an observable acceptance check. Complete the first milestone before adding workflow breadth.

### Milestone 1: A working spreadsheet coding service

| ID | Product backlog item | Acceptance check | Depends on |
| --- | --- | --- | --- |
| B01 | Reach a deployed web service from a staff laptop | A tester opens the agreed URL in Edge or Chrome on a standard Windows laptop, signs in and sees New coding run. A second signed-in user cannot access the first user's runs or files through guessed URLs or API requests. | Hosting and identity route |
| B02 | Check that coding is available | A maintainer checks application readiness and performs a small server-side provider request. A valid response confirms connectivity and authentication; an unavailable provider produces a clear message without exposing credentials. The UI does not claim that coding is available based on the web page loading alone. | B01 |
| B03 | Import an XLSX worksheet | A tester uploads a workbook, selects a sheet and sees matching headers, sample rows and row count. An invalid workbook or oversized upload produces an actionable error. The agreed first-release size limit appears beside the upload control. | B01 |
| B04 | Map one question and import a codebook | A tester selects a response column and supplies a question and existing Markdown codebook. The preview lists the expected codes. Duplicate chosen IDs, empty or duplicate codes and output-column collisions block submission with a reason. Generated IDs distinguish duplicate response text. | B03 |
| B05 | Submit one persistent coding job | The server records a run and its exact input, codebook, prompt and settings snapshot, then processes it outside the browser request. Submitting the same request twice does not create duplicate runs. Progress distinguishes eligible, completed, failed and blank rows. | B02, B04 |
| B06 | Download results that account for every row | The output preserves source row count, order and columns. Each valid predicted code maps to 1 and each unselected code to 0 for a successfully coded row. Blank or failed rows retain missing code values and explicit statuses. The download includes the run manifest and opens in Excel. | B05 |
| B07 | Demonstrate the complete workflow | A survey user completes upload → mapping → existing codebook → coding → download on an agreed test workbook. Check that every eligible response has either a valid result or an explained failure. Compare a fixed model-stub fixture with expected output to verify mechanics; inspect a small live-model sample with the survey lead to assess usefulness. | B06 |

**Milestone 1 exit:** a survey user can code one question from a spreadsheet with an existing codebook and retrieve accounted-for results from the deployed service. Label partial runs as partial. A fixed fixture proves data handling; a live-model sample provides an initial quality check. Neither establishes a formal accuracy or agreement claim.

### Milestone 2: Availability and recovery for pilot use

| ID | Product backlog item | Acceptance check | Depends on |
| --- | --- | --- | --- |
| B08 | Leave and return to a running job | Close the browser while a run is processing, reopen it, sign in and find the same run in My runs. Processing continued and the final download remains available. Refreshing or reopening does not resubmit the job. | B07 |
| B09 | Handle model failures and retry incomplete work | Simulate a timeout, rate limit, malformed output and missing response IDs. The service records errors and marks affected rows incomplete. Retry commits only missing results, preserves earlier successes and records attempts. Warn that an interrupted provider call may incur another charge. | B08 |
| B10 | Recover from a worker or application restart | Restart a worker mid-run and restart the web application. Persisted state remains accessible; incomplete work resumes or becomes eligible for an explicit retry, without duplicate output rows or lost completed results. | B09 |
| B11 | Make service failures visible and supportable | A maintainer can inspect web/worker health, queue age, last progress time and failure counts without reading survey comments. A stalled worker produces a visible delayed status and an operational alert through the agreed channel. Restore a test run and its artifacts from backup. | B10 |
| B12 | Check normal pilot availability and capacity | Agree expected workbook size, concurrent runs and service hours. Run a representative workload plus a second user accessing the UI; record queue time, throughput, failures and availability. Agree measurable targets with the service owner, then repeat the checks against those targets. | B11 |

**Milestone 2 exit:** users can return to jobs, recover incomplete work and obtain support when the service fails. Record observed availability and recovery behaviour. Do not claim an uptime percentage from a successful demonstration or an untested infrastructure choice.

### Milestone 3: Extend only in response to a requirement

These items are ordered candidates. Promote an item when a pilot user needs it and the preceding workflow works; an expressed need may change this order. The triggers below also define when the data model or permissions need to expand.

| ID | Requirement that triggers the item | Product backlog item and acceptance check |
| --- | --- | --- |
| B13 | Users receive CSV files or need several questions | Add CSV import or repeat the established question/codebook mapping within one workbook. Verify row identity, independent question statuses and unique output prefixes. Add only the requested format or mapping capability. |
| B14 | Users repeat runs with the same settings | Save reusable setups and add run comparison. A user restores mappings and settings without saving credentials; each new run retains its own snapshots. |
| B15 | Users need a different approved model | Add selection from server-managed model configurations. Each run identifies the chosen configuration; users cannot supply arbitrary endpoints or secrets. |
| B16 | Users must correct AI labels inside the service | Add a response review screen. Save the original AI prediction and human correction separately, recording who changed what and when. No new role is needed for reviewing one's own run. |
| B17 | Users have human-coded reference data and need agreement measures | Import reference labels and explicit code mappings. Report coverage, pooled and per-code agreement, support and confusion counts; verify against metric fixtures. Missing labels remain missing. Generalise the existing benchmark-specific evaluator first. |
| B18 | Several people need access to the same work | Introduce shared workspaces and owner/member permissions. Verify permitted collaboration and denied access from outside the workspace. |
| B19 | The pilot requires independent human coding and a reference set | Add assignments, independent submissions and disagreement resolution. Verify that coders cannot retrieve other labels before submission, then preserve submissions when recording adjudication. Add only the permissions needed for this separation. |
| B20 | The evaluation protocol requires a sealed holdout | Add controlled partitions, a frozen candidate and a validation-custodian permission. Verify restrictions across previews, discovery, runs, APIs and exports before sealed data enters the workflow. |
| B21 | Staff need to maintain codebooks within the service | Add structured codebook editing, version comparison and publication. Pin assignments and runs to versions; round-trip existing Markdown without dropping rules or examples. |
| B22 | Users need help creating a codebook | Add optional theme discovery using permitted development data. Produce a draft for human review and record its source sample and settings. |

## How to test the first coding run

Use a small synthetic workbook with a unique ID, a response column and an unrelated metadata column. Supply an existing codebook with two or three codes. Include responses with one applicable code, multiple codes, no applicable code, duplicate response text and a blank cell. Test invalid codebooks and duplicate selected IDs as separate rejected inputs.

Use a deterministic provider stub to check exact output labels and row alignment. Inject unknown code names, unexpected IDs, duplicate returned IDs and omitted IDs; require validation errors or incomplete statuses rather than silent acceptance. Do not convert invalid model output into a successful empty code set. Then run the same browser workflow against the configured live provider and review a small sample with the survey lead.

For the basic happy path, require a valid result for each eligible response. For injected failures, require a visible partial/failed state and an explanation for each affected row. An explicit successful empty code set is different from a missing prediction.

## Implementation kept proportionate

Reuse the Python coding engine behind a small FastAPI service. Use one React/TypeScript application with the three initial views: New coding run, Run status and results, and My runs. Use accessible form and table components with a restrained Bootstrap-style layout. Support keyboard access, clear field errors and visible job status from the first release.

Persist run records, input/codebook/prompt snapshots, batch attempts and output artifacts. Use a server-side worker with durable checkpoints. Choose the database, file storage and queue supported by the hosting team; confirm those choices through B08–B12. Keep the HTTP request separate from model execution so closing a laptop does not stop coding.

The current engine offers spreadsheet loading, Markdown code extraction, model calls and nullable coded columns. Add job-level and batch-level status, strict response validation, progress callbacks and checkpointing. Extract reusable services from the GUI rather than invoking Tkinter or depending on GUI settings. The existing evaluator assumes two benchmark concepts and fills some missing labels with zero; leave it outside the first release and fix those assumptions in B17.

Take an immutable run snapshot from the start so results remain traceable. Defer general project/dataset version management, structured codebook records, assignments and adjudication tables until their backlog items become requirements. Limit model requests to fields needed for coding. Keep credentials, uploaded data and diagnostic logs within the approved server environment and its access/retention rules.

## Decisions needed for the first increment

Confirm the test URL and hosting owner, sign-in route, configured model/provider, supported workbook size and one approved example workbook plus codebook. These decisions unblock B01–B07. Use test data while live-data permissions are pending.

Record functional success, coding usefulness, availability and recovery as separate findings. Later quality studies and broader governance gates in the project plan apply when that scope becomes active. Demonstrating spreadsheet coding is the first product milestone.
