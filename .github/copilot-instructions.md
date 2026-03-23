# DeepCode Agent Repository Instructions

## Frontend Scope

- DeepCode uses Reflex as the only supported Web UI runtime.
- Treat Streamlit as retired architecture.
- Do not implement new features in `deepcode/ui/`.

## Where To Make UI Changes

- Runtime/state/UI behavior: `deepcode_reflex/`
- Shared web constants/translations: `deepcode/web_shared/`

## Legacy Compatibility Path

- `deepcode/ui/` is a deprecated compatibility shell kept only to provide clear runtime errors and migration hints.
- If you must touch `deepcode/ui/`, only keep lightweight deprecation wrappers. Do not restore Streamlit dependencies.

## Token-Efficient Code Reading

- Prefer searching and editing Reflex paths first.
- Ignore historical Streamlit references in `docs/` unless the task is explicitly about project history or migration notes.
