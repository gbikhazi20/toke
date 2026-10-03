# toke

A tiny CLI that counts how many input tokens a piece of text costs, using the
official token-counting endpoints from Anthropic and OpenAI. It's a single
Python 3.9+ script that uses only the standard library: no packages to install
and no build step.

```console
$ toke 'Hello, world'
Input tokens: 11
Provider:     anthropic
Model:        claude-opus-4-8
```

## Install

```sh
git clone https://github.com/gbikhazi20/toke.git
cd toke
install -m 755 toke ~/.local/bin/toke   # or any other directory on your PATH
```

Set an API key for each provider you want to use:

```sh
export ANTHROPIC_API_KEY='your-key'
export OPENAI_API_KEY='your-key'       # only needed for --provider openai
```

## Usage

```sh
toke 'Hello, world'                    # inline text
printf '%s' 'Hello, world' | toke      # stdin
toke --file prompt.txt                 # a UTF-8 file
toke --text 'Hello, world'             # explicit inline text

toke --provider openai --model gpt-5.4 'Hello, world'
toke --provider openai --json --file prompt.txt
```

## Output

The default output shows the count, provider, and selected model:

```text
Input tokens: 1,234
Provider:     anthropic
Model:        claude-opus-4-8
```

Use `--quiet` (or `-q`) for just the integer and a newline, without thousands
separators, for scripts: `toke -q 'Hello, world'`. `--json` returns
`{"provider":"anthropic","model":"claude-opus-4-8","input_tokens":10}`
(illustrative count). Errors go to stderr with exit code 1; invalid arguments
use exit code 2. `--timeout SECONDS` changes the default 30-second request timeout.
`--quiet` and `--json` cannot be combined.

## Defaults

| Setting | Default | Environment override |
| --- | --- | --- |
| Provider | `anthropic` | `TOKE_PROVIDER` |
| Anthropic model | `claude-opus-4-8` | `TOKE_ANTHROPIC_MODEL` |
| OpenAI model | `gpt-5.4` | `TOKE_OPENAI_MODEL` |

`--provider` and `--model` override environment defaults. Model defaults are
provider-specific, so switching providers also selects that provider's model.
Any model ID can be supplied; the provider validates its availability and support.
Keys are read from `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; `.env` files are not
automatically loaded.

## List supported providers

```sh
toke --list-providers
toke --list-providers --quiet         # Provider IDs only, one per line
toke --list-providers --json
```

Shows supported provider IDs and their built-in default models. This runs locally
without API keys, network requests, or reading stdin. Environment overrides do
not change this catalog. JSON output contains a `providers` array with `id` and
`default_model` for each provider. Cannot be combined with input, `--list-models`,
`--provider`, or `--model`.

## List available models

```sh
toke -p openai -m                     # Shorthand: -m without a value lists models
toke -m                              # List models for the default provider
toke --list-models                    # Default provider (Anthropic)
toke --list-models --provider openai
toke --list-models --provider anthropic --json
```

Fetches the model catalog using the selected provider's API key and prints sorted
model IDs, one per line. Anthropic pagination is followed automatically. With
`--json`, returns `{"provider":"anthropic","models":["claude-opus-4-8", "..."]}`.
Listing does not read stdin and cannot be combined with text, `--file`, or a model ID.
`-m MODEL` (or `--model MODEL`) still selects a model for counting; `-m` or
`--model` without a value lists models. For example, `toke -p openai -m gpt-5.4
'Hello'` counts text with that model. Put the model ID immediately after `-m`.
Use a returned ID with `--model` when counting tokens. The catalog includes all
available model types; appearing here does not guarantee support for the token
counting endpoint (for example, OpenAI also lists image and audio models).

Both providers expose `GET /v1/models`:
[Anthropic reference](https://platform.claude.com/docs/en/api/models/list),
[OpenAI reference](https://developers.openai.com/api/reference/resources/models/methods/list).

## Input and count semantics

- Supply one inline string, `--text`, or `--file`; with no explicit input, stdin
  is read. Explicit input takes precedence over a pipe. `--file -` forces stdin.
- Files and stdin must be UTF-8. Whitespace, including trailing newlines and CRLF,
  is preserved. `echo` adds a newline; use `printf '%s'` to omit it.
- Use `-- '--text-starting-with-a-dash'` for a positional string starting with `-`.
- Text, including empty input, is sent to the selected provider. No completion is
  generated and no local tokenizer is used. Empty input is subject to the
  provider's validation and overhead; it is not automatically reported as zero.
- Counts represent API input for one user message and may include framing or
  system-added tokens. They are not a raw text-only tokenization or a complete
  application's prompt cost. Anthropic describes its count as an estimate that
  may differ slightly from actual message usage.

Endpoints and references:

- Anthropic: `POST https://api.anthropic.com/v1/messages/count_tokens`
  ([token counting documentation](https://platform.claude.com/docs/en/build-with-claude/token-counting)).
- OpenAI: `POST https://api.openai.com/v1/responses/input_tokens`
  ([token counting documentation](https://developers.openai.com/api/docs/guides/token-counting)).

## Tests

```sh
python3 -m unittest -v
```

Tests mock the HTTP boundary; they require neither credentials nor API requests.
