# Contributing to Keelson

Contributions are welcome. Keelson is shared by several projects, so we keep it
small and consistent, and discuss larger changes before they are built.

## Before you start

| Change | What to do |
|---|---|
| Bug fix, test, documentation, dependency update | Open a pull request |
| New or changed subject, payload, interface, protocol or specification rule | Open a **proposal** issue first |
| New connector | Open a **proposal** issue first |

A proposal is short. Please wait for a maintainer to agree to it before writing
code. We weigh two things: whether it fits Keelson, and whether we can maintain
it in the long run. Some good ideas are better placed in a repository of their
own.

## Pull requests

Branch from `dev` and target `dev`. We look for:

- **One change** per pull request.
- **Reuse** of the SDK and existing helpers.
- **Tests** that fail when the behaviour breaks.
- **Documentation** that matches the code.

Larger pull requests take longer to review, and those without an agreed
proposal may be closed.

## Development

- Setup, commands and git workflow: [CLAUDE.md](CLAUDE.md), and the `CLAUDE.md`
  in each directory
- Key space, naming and units:
  [protocol specification](docs/protocol-specification.md)

Building something on Keelson in another repository? Add the
[GitHub topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)
`keelson-message`, `keelson-processor` or `keelson-connector` so that others can
find it.
