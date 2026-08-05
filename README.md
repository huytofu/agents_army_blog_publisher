# Entourage Blog Publisher

## Overview

This repository contains the Entourage blog publisher agent. Its job is to turn rough blog ideas from S3 into complete static website assets:

- updated `blog/posts.json` metadata
- one generated blog article HTML file
- one generated cover image
- an updated source idea file marked as processed

The system is designed as a small agent team. One supervisor decides what should happen next, one writing agent expands the post, and two artifact agents produce local HTML/image files. Only the main publisher workflow is allowed to write to S3.

## See the Agent in Action

Every post the agent publishes goes live on the Entourage website:

- **Blog index:** [entourage-ai.life/blogs.html](https://entourage-ai.life/blogs.html)

The agent runs autonomously on a schedule (an EventBridge-triggered Lambda, roughly one run every 3 days). Each run picks up one unprocessed idea from S3, expands it into a full article, generates a cover image, and publishes the result. Everything you see on the blog index — the article text, HTML layout, and cover image — was produced end to end by the agent team with no human editing.

To watch it work in real time, note the newest post on the index page and check back after the next scheduled run: a new entry will appear with a fresh cover image and article.

## Core Architecture

The pipeline is a LangGraph workflow with four cooperating agents:

1. **`BlogPipelineAgent`** — the supervisor. It observes graph state and returns one strict JSON routing decision per round (expand, revise, generate artifacts, retry, publish, or fail). It never writes content or touches S3.
2. **`BlogExpansionAgent`** — the writer. It expands a rough idea into a structured post: title, slug, excerpt, Markdown body, SEO metadata, and an image brief.
3. **`HtmlAgent`** — artifact subagent that polishes the post for web readability and writes a local `index.html`.
4. **`ImageAgent`** — artifact subagent that enhances the visual brief into a generation prompt and creates a local `cover.jpg`.

The two artifact subagents run in parallel and only have local file tools. S3 writes (feed update, asset upload, marking the idea processed) are restricted to the main publisher nodes.

## Running Locally

Activate the virtual environment, then run a one-shot dry run (no S3 writes):

```bash
python -m blog_manager.workers.run_blog_job --dry-run --max-ideas 1
```

## Documentation

- `docs/PIPELINE.md`: agent workflow, graph structure, ReAct decisions, and permission boundaries.
- `docs/DEPLOYMENT.md`: Lambda container deployment with EventBridge Scheduler, IAM, environment variables, rollout, rollback, weekly digest and blog API Lambda handler overrides, and Fargate fallback.
