# Architecture

## Event package contract

An event is a directory:

```text
events/<event_id>/
  event.json
  input/
    raw_comments.csv
    comments.csv
    stimuli.csv
  interim/
  processed/
  outputs/
```

`event.json` is the single source of truth for:

- the time window and phase boundaries;
- decision days and seat count;
- the input column mapping and time formats;
- platform, language, and prompt context;
- validation variants and minimum daily sample size;
- optional demographic priors.

`simulation.infra.event.load_event()` validates the descriptor and
`event_paths()` resolves every path. `build_config()` deep-copies the base config
and injects event-local paths, so two events cannot share caches or outputs.

## Inputs

`input/comments.csv` is the canonical comment table produced by
`personas/import_event.py`:

```text
comment_id, author, body, published, published_date, post_id, post_title,
parent_id, depth, channel, source, likes, source_file
```

`published` is timezone-aware; `published_date` is the local date used for phase
assignment. `input/stimuli.csv` is the daily timeline:

```text
day, real_date, stimulus_type, title, news_content, source
```

`stimulus_type` is `fact`, `gov_comm`, or `new_topic`. In `counterfactual` mode,
`gov_comm` rows are skipped because the simulated government produces its own
communication.

## Persona pipeline

1. `import_event` maps columns, parses dates, deduplicates IDs, remaps parent
   IDs, filters to the event window, and writes an import report.
2. `clean` applies deterministic text rules and writes
   `interim/comments_clean.csv`.
3. `score` calls the LLM per comment and appends to
   `interim/comment_scores.jsonl`.
4. `author_big5_score` asks the LLM for one label set per author and appends to
   `interim/author_big5.jsonl`.
5. `build_agents_author` allocates seats by label weight and writes
   `processed/user.csv`.
6. `generate_initial_states` asks the LLM for first-day panic/trust/rumor and
   writes `processed/initial_states.csv`.
7. `prepare_stimulus` validates `input/stimuli.csv` and writes
   `processed/post.csv`.

All append-only caches live in the event's `interim/` directory. Re-running a
free step is safe; paid steps reuse cached records.

## Simulation loop

`simulation/core/environment.py` runs one day at a time:

1. On decision days, the analyst agent summarizes the day and the government
   agent allocates weights and selects a strategy.
2. Agents that are active today receive a personal information flow.
3. Each agent chooses an action, produces content if needed, then reflects and
   updates panic/trust/rumor.
4. Actions are written back in `aid` order for reproducibility.
5. Strategy actions run before or after agent browsing depending on
   `intervention_timing` (`split` or `post`).
6. The day's snapshot is recorded.

The information flow groups posts into:
- today's latest event updates;
- today's other content;
- recent background;
- outdated background.

Reflection prompts require the agent to state which latest/outdated post IDs it
relied on. This is audited in `record.json`.

## Prompt context

`personas/prompts.py` builds prompts from the event context:

- platform and language;
- event name and description;
- the responsible official body;
- optional partisan/polarization field;
- the archetype taxonomy.

There are no built-in case-specific prompts. The same code runs for Chinese or
English events by changing `event.json`.

## Fake LLM

`simulation/infra/fake_llm.py` returns deterministic, format-correct outputs for
actions, comments, reflections, government decisions, and analyst briefs.
`simulation.run --llm-type fake` therefore runs the whole loop without network
access. This is intended for tests and smoke checks, not research results.

## Generated vs committed files

Committed:

- `event.json`;
- `events/_template/input/*`;
- `events/demo/input/*` and `events/demo/interim/author_big5.jsonl`;
- all code, docs, and tests.

Ignored:

- imported `comments.csv`;
- cleaning/scores/initial-state caches (except the demo author cache);
- `processed/`;
- `outputs/`;
- logs and figures.

## Security hygiene

- API keys come from environment variables only.
- `sanitize_config()` replaces key fields before writing `record.json`.
- The release test scans for key-like tokens, private paths, paper materials, and
  case-specific strings.
- If a secret is ever committed, delete the repository and rebuild it rather than
  trying to hide it in a later commit.
