# Leaderboard

Bills judged: 50  |  Judge: `claude-sonnet-4-6 (parallel Claude Code subagents)`

| Summarizer | Passes all criteria | Mean cost/summary | Mean latency |
|---|---|---|---|
| z-ai/glm-5.2 | 84% (42/50) | $0.0066 | 19.8s |
| deepseek/deepseek-v4-pro | 82% (41/50) | $0.0023 | 14.9s |
| google/gemini-3.5-flash | 76% (38/50) | $0.0226 | 9.1s |
| openai/gpt-5.5 | 74% (37/50) | $0.0304 | 8.6s |
| anthropic/claude-opus-4.8 | 54% (27/50) | $0.0414 | 8.1s |
| CRS (human) | 62% (31/50) | — | — |

## Per-criterion pass rate

| Summarizer | Accurate | No hallucinations | States purpose | Changes to existing law | Exceptions & conditions | Effective dates & timing | Major provisions covered | Objective tone | Coherent | Concise | Correct entities | Correct figures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| z-ai/glm-5.2 | 98% | 98% | 98% | 98% | 100% | 97% | 98% | 98% | 98% | 84% | 98% | 98% |
| deepseek/deepseek-v4-pro | 96% | 92% | 100% | 98% | 96% | 97% | 98% | 100% | 98% | 90% | 100% | 100% |
| google/gemini-3.5-flash | 94% | 98% | 100% | 100% | 100% | 100% | 98% | 100% | 100% | 84% | 100% | 98% |
| openai/gpt-5.5 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 74% | 100% | 100% |
| anthropic/claude-opus-4.8 | 98% | 98% | 100% | 98% | 100% | 100% | 100% | 98% | 100% | 58% | 100% | 100% |
| CRS (human) | 92% | 94% | 98% | 95% | 72% | 76% | 76% | 100% | 100% | 96% | 100% | 96% |
