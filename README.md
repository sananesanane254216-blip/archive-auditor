# archive-auditor

Checks that the published Close Call sweep archive keeps up with the referee and that each record hashes to the file the referee signed, and spends inference to triage the open participant issues.

Counting is arithmetic; deciding what messages *are* is inference, and every call
is metered into `spend.jsonl`. Nothing is published unless `--publish` is passed.

```
python agent.py                 # measure, classify, print, publish nothing
python agent.py --publish       # ... and post the finding and the note
```

Inference auto-detects Ollama on localhost; set `AGENT_MODEL` to pick a model.
