# Design documents on Confluence

Two jobs: **start** from an HLD or LLD that already lives on Confluence, and **publish** Da Vinci's document to Confluence, as a new page or over the existing one. Both go through the Atlassian MCP server (`mcp__atlassian__*` tools). If it isn't connected, say so and how to add it (`claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp`, then authenticate). Until then the Word file and `<name>.confluence.md` still get built, ready to paste.

## Where the page lives

Confluence locations come only from this project and this user: the model's `confluence` block (a page Da Vinci already published or started from), the page URL the user gives, or the project's `confluence_wiki_url` (reported by `spine.py`; blank when the project has none) as the suggested parent. Otherwise ask for the space and parent page. Never pick a space yourself, and never reuse a location from another project or from memory. Confirm the site with the Atlassian tools' accessible resources before the first call.

## Start from an existing page

1. Read the page the user names (or find it by title in the space they name) through the Atlassian tools. Read only; never edit it at this step.
2. **Its structure.** When its headings differ from the outline in use, offer to adopt them as the project's outline (`<design_docs_folder>/templates/hld.md` or `lld.md`, headings plus a line on what each holds). That's how the organisation's format reaches the document before a Word template exists.
3. **Its content is claims, not sources.** The page isn't a BMad artifact, so nothing is cited to it. Go through its statements:
   - The spine or another BMad source already says it: cite that source.
   - It contradicts the spine: put both to the user, most consequential first. The spine changes only on approval of the exact AD text.
   - The BMad sources don't say it: put it to the user as a challenge item. If approved, it becomes AD text in the spine and is cited from there; if deferred or rejected, it becomes a gap or is dropped.

   Tell the user up front how many statements fall in each group, so the size of the job is clear.
4. Record the page in the model's `confluence` block (below), then write and build as usual. Publishing the result back over that page is the next job, and waits for the user.

## Publish

1. Build with `--formats docx,confluence` (or just `confluence`). The build writes `<name>.confluence.md` and reports its `sha256`, the page `title`, and the diagram PNGs to attach.
2. **Look before writing.** For a page in the model's `confluence` block, fetch it and compare its current version with the recorded `version`. If it moved on, someone edited it on Confluence: show what changed against the last published `<name>.confluence.md`, carry the changes the user wants to keep into the model (as AD text first when they change a decision), rebuild, and only then go on. Never overwrite someone's edit unseen. For a new page, search the parent for a page with the same title, and ask whether to update it or create a new one.
3. **Ask.** Say exactly what will happen, then wait for a yes: create or update, the title, the space and parent, and for an update that it replaces the page body (Confluence keeps the old version in its history).
4. Create or update the page with the Markdown body, in the body format the Atlassian tool's schema asks for; convert when it wants something else. Diagrams: attach the PNGs the build listed if the tools can upload attachments. Otherwise tell the user which files to attach where; each figure keeps its caption in the page.
5. Record the result in the model:

   ```json
   "confluence": {"site": "https://<site>.atlassian.net", "space": "ARCH", "parent_id": "12345", "page_id": "67890",
                  "url": "https://...", "version": 4, "sha256": "<the build's sha256>", "published": "2026-09-28"}
   ```

   Then report the page link. Bump the model's `version` for each issued publish.

The Word file and the page come from the same model and the same build, so they always say the same thing. When the user edits one, carry the edit into the model and rebuild both.
