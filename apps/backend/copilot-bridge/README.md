# Copilot Bridge Server

Node.js HTTP server that wraps the GitHub Copilot CLI SDK, providing a Python-accessible API for the Auto-Copilot backend.

## Architecture

```
Python Backend → HTTP → Copilot Bridge → @github/copilot SDK → GitHub Copilot API
```

## Installation

```bash
cd apps/backend/copilot-bridge
npm install
```

## Running

```bash
npm start
```

The server listens on port 9545 by default (configurable via `COPILOT_BRIDGE_PORT`).

## API Endpoints

### Health Check
```
GET /health
Response: { "status": "ok", "sessions": 0 }
```

### Create Session
```
POST /session/create
Body: {
  "model": "gpt-4o",
  "workingDirectory": "/path/to/project",
  "availableTools": ["Read", "Write", "Edit", "Bash"],
  "mcpServers": {},
  "systemMessage": "You are an expert developer..."
}
Response: { "sessionId": "abc123", "model": "gpt-4o" }
```

### Query Session (Streaming)
```
POST /session/:sessionId/query
Body: { "prompt": "Create a new file..." }
Response: Server-Sent Events stream
```

### Abort Session
```
POST /session/:sessionId/abort
Response: { "success": true }
```

### Delete Session
```
DELETE /session/:sessionId
Response: { "success": true }
```

### List Sessions
```
GET /sessions
Response: { "sessions": [{ "sessionId": "abc123", "model": "gpt-4o" }] }
```

## Event Stream Format

The query endpoint returns Server-Sent Events (SSE) with the following event types:

- `connected` - Initial connection established
- `assistant.message` - Text from the assistant
- `assistant.tool_use` - Tool being used
- `tool.result` - Tool execution result
- `error` - Error occurred
- `done` - Query complete

## Requirements

- Node.js >=22.0.0
- GitHub Copilot subscription
- GitHub authentication (via GITHUB_TOKEN or gh CLI)
