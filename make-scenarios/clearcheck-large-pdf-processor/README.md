# SOC 2 ClearCheck Backup - Large PDF Processor (Make scenario 5730339)

Scenario: https://us2.make.com/316751/scenarios/5730339/edit

## Problem

Runs on large PDFs failed with:

```
RuntimeError: The Code execution timed out.   (module: code:ExecuteCode)
```

The failure happened in the first **Make Code** module on each router branch
(the "PDF text chunker", modules 6 and 61). That module received the entire
extracted PDF text (tens of MB for the failing runs) and split it into
600,000-character chunks in JavaScript.

Make's Code app runs in a sandbox with a hard limit of **30 seconds of
execution time, 1 CPU and 512 MB RAM** on Core/Pro/Teams plans. The two failed
executions each billed about 60 Code-app credits (2 credits per second), i.e.
the chunker ran for the full 30 seconds and was killed. Rewriting the JS could
not make a 40-50 MB string reliably fit in that budget, and every failure
auto-deactivated the webhook scenario ("Fix the error or clear the queue").

## Fix (applied directly to the live scenario)

### Round 1: no more Code-app chunker

The Code chunker + Iterator pair was replaced with native Make modules
(Set variables + Repeater + `substring()`), which have no sandbox time limit.
Chunk size was also reduced from 600,000 to 350,000 characters: the first live
run showed dense evidence text tokenizing at about 2.9 chars per token, so one
600,000-char chunk reached 209,318 tokens and Claude rejected it
(`[400] prompt is too long: 209318 tokens > 200000 maximum`).

```
totalChunks = if(len <= 350000; 1; ceil((len - 350000) / 345000) + 1)
chunk i     = substring(pdfText; (i - 1) * 345000; (i - 1) * 345000 + 350000)
```

This fixed the reported error: 41 queued jobs then ran through cleanly.

### Round 2: the scenario chains itself so any file size fits Make's 40-minute limit

The three largest stranded files (9.9 MB, 18 MB and 22.6 MB PDFs, each with
well over 25 MB of extracted text) then hit a different ceiling. Make stops any
single execution after 40 minutes (+5 min grace), and Make sends chunks to
Claude one after another at ~35 s each, so 67-77 chunks could not finish in one
run. All three runs ended with a warning at exactly 45:00 before the final merge
and callback.

The scenario now splits a job across several of its own executions:

| Route | Filter | What it does |
|---|---|---|
| 1 "from google drive" | `file.google_drive_file_id` exists AND `continuation.text_url` does not | Google Drive download -> PDF.co text extraction (link valid 1440 min) -> module 78 POSTs the job back to this scenario's own webhook with `continuation.text_url`, `next_chunk = 1`, `merged_so_far = ""` |
| 2 "from attachment URL" | `file.download_url` exists AND `continuation.text_url` does not | same, from the download URL |
| 3 "continuation: process chunk batch" | `continuation.text_url` exists | downloads the text, computes the chunk count, runs Step 1 on at most **15 chunks** (`next_chunk` .. `next_chunk+14`), runs Step 2 to merge this batch into `merged_so_far`, then either POSTs itself again with `next_chunk + 15` and the new merged result, or, on the last batch, runs the parser (fail-soft version) and the Supabase callback |

Each execution therefore stays around 10-15 minutes. Step 2 never sees more
than one batch of chunk outputs plus the single running merged result, which also
keeps the final merge inside Claude's 200k-token window regardless of file size.
A 76-chunk document takes about six chained executions (roughly an hour of wall
time) but completes and reports back.

The continuation payload carries the original `contract_version`, `job_id`,
`sync_run_id`, `file`, `control`, `extraction` and `callback` objects
(serialized with `toJSON()`), so the processing route needs nothing else.

Known limits left as they were:

- An unhandled error (PDF.co failure, Claude API error) still stops the run and,
  because the trigger is a webhook, deactivates the scenario until it is turned
  back on. Jobs received while it is off wait in the webhook queue.
- Empty extracted text stops the run at the "PDF text not empty" filter without
  a callback, exactly like the old "No text from PDF.co" error.

## Files

- `blueprint.before.json` - export of the scenario before the fix
- `blueprint.after.json` - blueprint that is now deployed (round 2)
- `build_blueprint.py` - script that produced it from the round-1 blueprint
