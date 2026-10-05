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

The Code chunker + Iterator pair on each branch was replaced with native Make
modules, which have no sandbox time limit:

| Old module | New module | Purpose |
|---|---|---|
| 6 / 61 `code:ExecuteCode` (chunker) | 74 / 76 `util:SetVariables` | Stores `pdfText`, `pdfLength`, `totalChunks` once per run |
| 15 / 62 `builtin:BasicFeeder` (Iterator) | 75 / 77 `builtin:BasicRepeater` | Emits one bundle per chunk (`i` = chunk number) |

Chunking math (same 600,000-char chunk size the old code used, plus a
5,000-char overlap so a record cut at a boundary is still whole in the next chunk):

```
totalChunks = if(len <= 600000; 1; ceil((len - 600000) / 595000) + 1)
chunk i     = substring(pdfText; (i - 1) * 595000; (i - 1) * 595000 + 600000)
```

Downstream modules were rewired:

- Claude "Step 1" modules 30 / 63: `evidence_text`, `chunk_number`,
  `total_chunks` now come from the Set-variables + Repeater modules.
- Array aggregators 17 / 64: source changed from the Iterator to the Repeater.
- A filter "PDF text not empty" on the Set-variables module stops the run when
  PDF.co returned no text (the old code threw "No text from PDF.co").

The Step-1 -> Text aggregator -> Step-2 -> parser -> Supabase callback chain is
unchanged. The Code modules that parse Claude's final answer (21 / 68) still
exist; they handle a small input and were never the problem.

## Files

- `blueprint.before.json` - export of the scenario before the fix
- `blueprint.after.json` - blueprint that is now deployed
