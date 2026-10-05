import json, copy, re

MAX_CHARS = 350000
OVERLAP   = 5000
STEP      = MAX_CHARS - OVERLAP          # 345000
BATCH     = 15                           # chunks per execution (~15 x 40 s = 10 min, well under Make's 40 min; keeps Step-2 input bounded)
HOOK_URL  = "https://hook.us2.make.com/z1xgiq8mcp9dsbddfi9f5z3976s2nx4d"
PDFCO_EXPIRATION = 1440                  # minutes the PDF.co text link stays valid (chained runs re-download it)

src = json.load(open("fixed_blueprint.json"))   # local 350k-chunk blueprint (matches what is deployed)
def walk(flow):
    for m in flow:
        yield m
        for r in m.get("routes", []) or []: yield from walk(r.get("flow", []))
        for oe in m.get("onerror", []) or []: yield from walk([oe])
M = {m["id"]: copy.deepcopy(m) for m in walk(src["flow"])}

def http_send(mid, name, body, x, y):
    return {"id": mid, "module": "http:ActionSendData", "version": 3,
            "parameters": {"handleErrors": False, "useNewZLibDeCompress": True},
            "mapper": {"ca": "", "qs": [], "url": HOOK_URL, "data": body, "gzip": True, "method": "post",
                       "headers": [{"name": "Content-Type", "value": "application/json"}],
                       "timeout": "", "useMtls": False, "bodyType": "raw", "contentType": "application/json",
                       "serializeUrl": False, "shareCookies": False, "parseResponse": False,
                       "followRedirect": True, "useQuerystring": False, "followAllRedirects": False,
                       "rejectUnauthorized": True},
            "metadata": {"designer": {"x": x, "y": y, "name": name},
                         "restore": {"expect": {"qs": {"mode": "chose"}, "method": {"mode": "chose", "label": "POST"},
                                                "headers": {"mode": "chose", "items": [None]},
                                                "bodyType": {"label": "Raw"}, "contentType": {"label": "JSON (application/json)"}}}}}

def handoff_body(url_expr):
    return ("{\n"
            '  "contract_version": {{toJSON(2.contract_version)}},\n'
            '  "job_id": {{toJSON(2.job_id)}},\n'
            '  "sync_run_id": {{toJSON(2.sync_run_id)}},\n'
            '  "file": {{toJSON(2.file)}},\n'
            '  "control": {{toJSON(2.control)}},\n'
            '  "extraction": {{toJSON(2.extraction)}},\n'
            '  "callback": {{toJSON(2.callback)}},\n'
            '  "continuation": {\n'
            f'    "text_url": {{{{toJSON({url_expr})}}}},\n'
            '    "next_chunk": 1,\n'
            '    "merged_so_far": ""\n'
            "  }\n"
            "}")

continuation_body = ("{\n"
            '  "contract_version": {{toJSON(2.contract_version)}},\n'
            '  "job_id": {{toJSON(2.job_id)}},\n'
            '  "sync_run_id": {{toJSON(2.sync_run_id)}},\n'
            '  "file": {{toJSON(2.file)}},\n'
            '  "control": {{toJSON(2.control)}},\n'
            '  "extraction": {{toJSON(2.extraction)}},\n'
            '  "callback": {{toJSON(2.callback)}},\n'
            '  "continuation": {\n'
            '    "text_url": {{toJSON(2.continuation.text_url)}},\n'
            '    "next_chunk": {{82.batchEnd + 1}},\n'
            '    "merged_so_far": {{toJSON(88.textResponse)}}\n'
            "  }\n"
            "}")

# ---------------- Route 1: Google Drive -> PDF.co -> hand off ----------------
m3 = M[3]
m3["filter"] = {"name": "from google drive", "conditions": [[
    {"a": "{{2.file.google_drive_file_id}}", "o": "exist"},
    {"a": "{{2.continuation.text_url}}", "o": "notexist"}]]}
m4 = M[4]; m4["mapper"]["expiration"] = PDFCO_EXPIRATION
m4["metadata"]["designer"]["name"] = "PDF.co: PDF to text"
m78 = http_send(78, "Hand off to chunk processor (this scenario)", handoff_body("4.url"), -668, -601)
route1 = [m3, m4, m78]

# ---------------- Route 2: download URL -> PDF.co -> hand off ----------------
m54 = M[54]
m54["filter"] = {"name": "from attachment URL", "conditions": [[
    {"a": "{{2.file.download_url}}", "o": "exist"},
    {"a": "{{2.continuation.text_url}}", "o": "notexist"}]]}
m60 = M[60]; m60["mapper"]["expiration"] = PDFCO_EXPIRATION
m60["metadata"]["designer"] = {"x": -1102, "y": 84, "name": "PDF.co: PDF to text"}
m79 = http_send(79, "Hand off to chunk processor (this scenario)", handoff_body("60.url"), -802, 84)
route2 = [m54, m60, m79]

# ---------------- Route 3: process one batch of chunks ----------------
Y = 700
m80 = {"id": 80, "module": "http:ActionGetFile", "version": 3, "parameters": {"handleErrors": False},
       "filter": {"name": "continuation: process chunk batch", "conditions": [[{"a": "{{2.continuation.text_url}}", "o": "exist"}]]},
       "mapper": {"url": "{{2.continuation.text_url}}", "method": "get", "serializeUrl": False, "shareCookies": False},
       "metadata": {"designer": {"x": -1376, "y": Y, "name": "Download extracted text"}, "restore": {}}}

L = "length(toString(80.data))"
m81 = {"id": 81, "module": "util:SetVariables", "version": 1, "parameters": {},
       "filter": {"name": "PDF text not empty", "conditions": [[{"a": f"{{{{{L}}}}}", "o": "number:greater", "b": "0"}]]},
       "mapper": {"scope": "roundtrip", "variables": [
           {"name": "pdfText", "value": "{{toString(80.data)}}"},
           {"name": "pdfLength", "value": f"{{{{{L}}}}}"},
           {"name": "totalChunks", "value": f"{{{{if({L} <= {MAX_CHARS}; 1; ceil(({L} - {MAX_CHARS}) / {STEP}) + 1)}}}}"}]},
       "metadata": {"designer": {"x": -1076, "y": Y, "name": "Prepare PDF text + chunk count"},
                    "restore": {"expect": {"scope": {"label": "One cycle"}, "variables": {"items": [None, None, None]}}},
                    "interface": [{"name": "pdfText", "type": "text", "label": "pdfText"},
                                  {"name": "pdfLength", "type": "number", "label": "pdfLength"},
                                  {"name": "totalChunks", "type": "number", "label": "totalChunks"}]}}

N = "2.continuation.next_chunk"
m82 = {"id": 82, "module": "util:SetVariables", "version": 1, "parameters": {},
       "mapper": {"scope": "roundtrip", "variables": [
           {"name": "batchStart", "value": f"{{{{{N}}}}}"},
           {"name": "batchEnd", "value": f"{{{{if({N} + {BATCH-1} >= 81.totalChunks; 81.totalChunks; {N} + {BATCH-1})}}}}"},
           {"name": "batchSize", "value": f"{{{{if(81.totalChunks - {N} + 1 > {BATCH}; {BATCH}; 81.totalChunks - {N} + 1)}}}}"},
           {"name": "batchStatus", "value": f"{{{{if({N} + {BATCH-1} >= 81.totalChunks; \"last\"; \"more\")}}}}"}]},
       "metadata": {"designer": {"x": -776, "y": Y, "name": f"Batch window (max {BATCH} chunks per run)"},
                    "restore": {"expect": {"scope": {"label": "One cycle"}, "variables": {"items": [None, None, None, None]}}},
                    "interface": [{"name": "batchStart", "type": "number", "label": "batchStart"},
                                  {"name": "batchEnd", "type": "number", "label": "batchEnd"},
                                  {"name": "batchSize", "type": "number", "label": "batchSize"},
                                  {"name": "batchStatus", "type": "text", "label": "batchStatus"}]}}

m83 = {"id": 83, "module": "builtin:BasicRepeater", "version": 1, "parameters": {},
       "mapper": {"start": "{{82.batchStart}}", "repeats": "{{82.batchSize}}", "step": 1},
       "metadata": {"designer": {"x": -476, "y": Y, "name": "Chunk repeater (1 bundle per chunk)"}, "restore": {}}}

m84 = copy.deepcopy(M[30]); m84["id"] = 84
t = m84["mapper"]["messages"][0]["content"][0]["text"]
old = f"substring(74.pdfText; (75.i - 1) * {STEP}; (75.i - 1) * {STEP} + {MAX_CHARS})"
assert old in t
t = (t.replace(old, f"substring(81.pdfText; (83.i - 1) * {STEP}; (83.i - 1) * {STEP} + {MAX_CHARS})")
       .replace('"/\\{\\{chunk_number\\}\\}/g"; 75.i', '"/\\{\\{chunk_number\\}\\}/g"; 83.i')
       .replace('"/\\{\\{total_chunks\\}\\}/g"; 74.totalChunks', '"/\\{\\{total_chunks\\}\\}/g"; 81.totalChunks'))
assert "74." not in t and "75." not in t
m84["mapper"]["messages"][0]["content"][0]["text"] = t
m84["metadata"]["designer"] = {"x": -176, "y": Y, "name": "Step 1: chunk extractor"}

m85 = copy.deepcopy(M[17]); m85["id"] = 85; m85["parameters"]["feeder"] = 83
for k, v in list(m85["mapper"].items()):
    m85["mapper"][k] = v.replace("{{30.", "{{84.")
m85["mapper"]["value"] = "{{83.i}}"; m85["mapper"]["__IMTINDEX__"] = "{{83.i}}"; m85["mapper"]["__IMTLENGTH__"] = "{{82.batchSize}}"
m85["metadata"]["designer"] = {"x": 124, "y": Y, "name": "Collect chunk results"}

m86 = copy.deepcopy(M[52]); m86["id"] = 86; m86["parameters"]["feeder"] = 85
m86["mapper"]["value"] = "{{85.array[].textResponse}}"
m86["metadata"]["designer"] = {"x": 424, "y": Y, "name": "Join chunk results"}

PRIOR = "ifempty(2.continuation.merged_so_far; emptystring)"
merge_input = (
    f'{{{{if(length({PRIOR}) > 0; "### ALREADY MERGED RESULT OF THE EARLIER CHUNKS OF THIS SAME DOCUMENT (a Stage-2 synthesized JSON). '
    'Treat it as a pre-merged chunk extraction: merge the new chunk extractions below into it and preserve all of its content. ###"; "")}}\n'
    f'{{{{{PRIOR}}}}}\n'
    f'{{{{if(length({PRIOR}) > 0; "### NEW CHUNK EXTRACTIONS FOR CHUNKS "; "")}}}}{{{{if(length({PRIOR}) > 0; 82.batchStart; "")}}}}{{{{if(length({PRIOR}) > 0; " TO "; "")}}}}{{{{if(length({PRIOR}) > 0; 82.batchEnd; "")}}}}{{{{if(length({PRIOR}) > 0; " OF "; "")}}}}{{{{if(length({PRIOR}) > 0; 81.totalChunks; "")}}}}{{{{if(length({PRIOR}) > 0; " ###"; "")}}}}\n'
    '{{86.text}}')
m87 = {"id": 87, "module": "util:SetVariables", "version": 1, "parameters": {},
       "mapper": {"scope": "roundtrip", "variables": [{"name": "mergeInput", "value": merge_input}]},
       "metadata": {"designer": {"x": 724, "y": Y, "name": "Compose Step-2 input (earlier merge + this batch)"},
                    "restore": {"expect": {"scope": {"label": "One cycle"}, "variables": {"items": [None]}}},
                    "interface": [{"name": "mergeInput", "type": "text", "label": "mergeInput"}]}}

m88 = copy.deepcopy(M[18]); m88["id"] = 88
t = m88["mapper"]["messages"][0]["content"][0]["text"]
assert '"/\\{\\{chunk_extractions\\}\\}/g"; 52.text' in t
t = t.replace('"/\\{\\{chunk_extractions\\}\\}/g"; 52.text', '"/\\{\\{chunk_extractions\\}\\}/g"; 87.mergeInput')
m88["mapper"]["messages"][0]["content"][0]["text"] = t
m88["metadata"]["designer"] = {"x": 1024, "y": Y, "name": "Step 2: merge this batch into the running result"}

m90 = http_send(90, "Continue with next batch (this scenario)", continuation_body, 1624, Y - 150)

m91 = copy.deepcopy(M[21]); m91["id"] = 91
m91["mapper"]["input"] = [
    {"name": "rawInput", "value": "{{88.textResponse}}"},
    {"name": "jobId", "value": "{{2.job_id}}"},
    {"name": "providerExecutionId", "value": "{{var.scenario.executionId}}"},
    {"name": "providerExecutionUrl", "value": "{{var.scenario.executionUrl}}"},
    {"name": "inputTokens", "value": "{{88.usage.input_tokens}}"},
    {"name": "outputTokens", "value": "{{88.usage.output_tokens}}"}]
m91["metadata"]["restore"]["expect"]["input"]["items"] = [None] * 6
m91["metadata"]["designer"] = {"x": 1624, "y": Y + 150, "name": "Parse final result + build callback"}
m91["filter"] = {"name": "last batch: finish", "conditions": [[{"a": "{{82.batchStatus}}", "o": "text:equal", "b": "last"}]]}
m90["filter"] = {"name": "more chunks remain", "conditions": [[{"a": "{{82.batchStatus}}", "o": "text:equal", "b": "more"}]]}

m92 = copy.deepcopy(M[37]); m92["id"] = 92
m92["mapper"]["data"] = "{{91.result.callbackBody}}"
m92["metadata"]["designer"] = {"x": 1924, "y": Y + 150, "name": "call back supabase"}
m93 = m92["onerror"][0]; m93["id"] = 93
m93["mapper"]["data"] = m93["mapper"]["data"].replace("37.error.message", "92.error.message")
assert "92.error.message" in m93["mapper"]["data"]
m93["metadata"]["designer"] = {"x": 1924, "y": Y + 400, "name": "call back supabase (failed)"}

m89 = {"id": 89, "module": "builtin:BasicRouter", "version": 1, "mapper": None,
       "routes": [{"flow": [m90]}, {"flow": [m91, m92]}],
       "metadata": {"designer": {"x": 1324, "y": Y}}}
route3 = [m80, m81, m82, m83, m84, m85, m86, m87, m88, m89]

bp = copy.deepcopy(src)
for k in ("scheduling", "interface"): bp.pop(k, None)
router = next(m for m in bp["flow"] if m["module"] == "builtin:BasicRouter")
router["routes"] = [{"flow": route1}, {"flow": route2}, {"flow": route3}]

s = json.dumps(bp)
ids = [m["id"] for m in walk(bp["flow"])]
assert len(ids) == len(set(ids)), "duplicate ids"
for dead in [72, 73, 74, 75, 76, 77, 30, 17, 52, 18, 20, 21, 37, 39, 63, 64, 65, 66, 67, 68, 69, 70]:
    assert not re.search(r"\{\{%d\." % dead, s), f"dangling ref to {dead}"
    assert dead not in ids
json.dump(bp, open("v3_blueprint.json", "w"), indent=1, ensure_ascii=False)
def show(flow, d=0):
    for m in flow:
        print("  "*d, m["id"], m["module"], "|", (m.get("filter") or {}).get("name") or "", "|", m.get("metadata", {}).get("designer", {}).get("name", ""))
        for r in m.get("routes", []) or []:
            print("  "*d, "  route:"); show(r["flow"], d+2)
        for oe in m.get("onerror", []) or []:
            print("  "*d, "  onerror:"); show([oe], d+2)
show(bp["flow"])
print("modules:", len(ids), "| compact bytes:", len(json.dumps(bp, separators=(',', ':'), ensure_ascii=False)))
