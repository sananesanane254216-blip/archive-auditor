# archive-auditor

Audits Flop's public records: hashes the newest sweep records of every Technocore contest archive when it grows, counts open issues and pull requests and the last merge on every flop-labs repository, and spends inference to sort the open issues.

Counting is arithmetic; deciding what messages *are* is inference, and every call
is metered into `spend.jsonl`. Nothing is published unless `--publish` is passed.

```
python agent.py                 # measure, classify, print, publish nothing
python agent.py --publish       # ... and post the finding and the note
```

Inference auto-detects Ollama on localhost; set `AGENT_MODEL` to pick a model.
