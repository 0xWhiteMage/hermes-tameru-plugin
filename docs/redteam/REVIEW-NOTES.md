# Review status: exploratory, not release evidence

The 1.6.0 result files are frozen observations of the initial harness. They are not approved as a complete safety or performance evaluation.

- Engine idempotence currently checks only second-pass output growth. It does not establish byte equality.
- The extraction checker accepts counted-line and last-line regexes before validating their bodies, accepts a shared 60-character prefix as a truncated line, removes whitespace inside JSON strings, and accepts broad JSON marker substrings. Its zero-fabrication result does not establish exact extraction or semantic preservation.
- Hash-seed checks select the first 60 cases (hostile input, traces, compiler output, and eight test-runner cases), with no language/format/attack coverage in that sample. Two missing hash outputs compare equal. Timing is a single sample per case with concurrent workers, not a serial scaling proof.
- The cross-session store probe manually shares one OriginalStore. Hermes normally clones engines per agent and clears stores at session boundaries. A production lifecycle reproduction is required before calling this a cross-session leak.
- Forged-header parsing demonstrates syntax acceptance; the full demotion/recovery path and trust consequences still need a production-path reproduction.
- Several plugin argument probes classify any successfully parsed answer as ok/info rather than assert the expected result. The clone-empty check starts with an empty parent. The timeout probe permits either an error or content and does not establish that a timed-out child was killed.
- Retaining explicitly labelled old and current config values is not by itself a safety leak. The supersession finding requires a scoped behavioral contract that preserves historical queries and avoids unsafe code/config reassignment inference.
- No savings, unchanged output, and low compression risk are not contradictory by themselves. Blank-only inputs need character accounting separate from token savings.
- The newer Hermes checkout supports in-chat slash replies, native choice pickers, unified Desktop assets, composer contributions, and transcript directives. The older compatibility pin's UI limitation must not be presented as current Hermes capability.

Next checks and any revised evaluation logic are owned by the lead. Raw baseline results must remain unchanged; later results need distinct provenance and output paths. Do not publish this directory or claim all reported findings are confirmed vulnerabilities.
