## Overview

This repo is for blog-publisher agent related codes

Core architecture:
1. Main blog idea expanding agent
2. Two sub agents to generate blog artifacts

## Documentation

- `PIPELINE.md`: agent workflow, graph structure, ReAct decisions, and permission boundaries.
- `DEPLOYMENT.md`: Lambda container deployment with EventBridge Scheduler, IAM, environment variables, rollout, rollback, weekly digest Lambda handler override, and Fargate fallback.