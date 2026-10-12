# Dev Agent Group

Four roles onboard a source table together: Coordinate, Architect, Implement, and Review. The UI is in `front_end`. The FastAPI service is in `back_end`. The model is DeepSeek.

Jira is not connected. Group and role chats produce plans and reviews; repository actions are not executed by the chat agents. The GitHub page provides separate, user-triggered repository operations.

## Pages

- Group run: one task moves through intake and summary, calling the agents enabled in Coordinate's trigger settings. You can also ask a single role; configured delegates run in the same chat. Switch between Timeline and Board.
- Four chats: talk with Coordinate, Architect, Implement, or Review on its own.
- Agent capabilities: choose which agents each role may trigger and whether it gets a GitHub workspace shortcut.
- GitHub: configure a repository and token, browse branches and files, commit text-file edits, view commit history, create or merge pull requests, and create or close issues.

Role definitions come from the `*.agent.md` files in the `default_promt/` directory.

## Start

```bash
cd back_end
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put `DEEPSEEK_API_KEY` in `back_end/.env`, or save it from the page after startup. Saving from the page writes `.env`.

Configure the GitHub repository and personal access token from the GitHub page after startup. The token is stored in the ignored `back_end/.env` file and is never returned to the browser. Use a fine-grained token limited to the selected repository with metadata, contents, pull request, and issue permissions needed for your workflow.

```bash
./run.sh
```

Open http://127.0.0.1:8000 .

The default model is `deepseek-flash`. A full run calls the model once for each enabled workflow step. For a harder design, switch to `deepseek-v4-pro` in settings.
