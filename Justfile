set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

mock-bin := justfile_directory() + "/target/mock-tools/bin"

mock-tools:
    ./scripts/install-mock-tools.sh

ci: mock-tools
    cargo fmt --all -- --check
    cargo clippy --all-targets --locked -- -D warnings
    cargo test --locked
    # Real-tool tests launch genuine claude/codex processes (each tens of threads,
    # with two alive at once during the fork phase). On a dev box already running
    # agents this can brush the soft nproc limit and make the tool's own hook
    # `posix_spawn` fail with EAGAIN. Raise the soft limit to the hard ceiling for
    # these lines so the tests aren't flaky against a busy machine.
    ulimit -Su "$(ulimit -Hu)" && PATH="{{mock-bin}}:$PATH" cargo test --locked --test real_tool_codex -- --ignored --nocapture --test-threads=1
    ulimit -Su "$(ulimit -Hu)" && PATH="{{mock-bin}}:$PATH" cargo test --locked --test real_tool_claude -- --ignored --nocapture --test-threads=1
    ulimit -Su "$(ulimit -Hu)" && PATH="{{mock-bin}}:$PATH" cargo test --locked --test test_relay_roundtrip -- --ignored --nocapture --test-threads=1
