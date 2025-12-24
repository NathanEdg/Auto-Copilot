#!/usr/bin/env node

/**
 * GitHub Copilot Bridge Server
 * 
 * Provides a Python-accessible HTTP API for the GitHub Copilot CLI SDK.
 * This bridge allows the Python backend to use Copilot's agentic capabilities
 * while maintaining the existing architecture.
 */

import express from 'express';
import bodyParser from 'body-parser';
import { query, LocalSession } from '@github/copilot/sdk';

const app = express();
const PORT = process.env.COPILOT_BRIDGE_PORT || 9545;

// Store active sessions
const sessions = new Map();

app.use(bodyParser.json({ limit: '50mb' }));

/**
 * Health check endpoint
 */
app.get('/health', (req, res) => {
  res.json({ status: 'ok', sessions: sessions.size });
});

/**
 * Create a new session
 * POST /session/create
 * Body: {
 *   model: string,
 *   workingDirectory: string,
 *   availableTools: string[],
 *   mcpServers: object,
 *   systemMessage: string
 * }
 */
app.post('/session/create', async (req, res) => {
  try {
    const {
      model = 'gpt-4o',
      workingDirectory,
      availableTools,
      mcpServers,
      systemMessage,
      sessionId
    } = req.body;

    const session = new LocalSession({
      model,
      workingDirectory,
      availableTools,
      mcpServers,
      systemMessage: systemMessage ? { content: systemMessage } : undefined,
      sessionId
    });

    const id = session.sessionId;
    sessions.set(id, session);

    res.json({
      sessionId: id,
      model: session._selectedModel || model
    });
  } catch (error) {
    console.error('Error creating session:', error);
    res.status(500).json({ error: error.message, stack: error.stack });
  }
});

/**
 * Send a query to a session and stream responses
 * POST /session/:sessionId/query
 * Body: { prompt: string }
 */
app.post('/session/:sessionId/query', async (req, res) => {
  try {
    const { sessionId } = req.params;
    const { prompt } = req.body;

    const session = sessions.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: 'Session not found' });
    }

    // Set up SSE (Server-Sent Events) for streaming
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    // Send initial connection confirmation
    res.write('data: {"type":"connected"}\n\n');

    try {
      // Subscribe to session events
      session.on('*', (event) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`);
      });

      // Send the prompt
      await session.send({ prompt });

      // Wait for completion
      // Note: Session events are streamed via the event handler above
      
    } catch (error) {
      res.write(`data: ${JSON.stringify({ type: 'error', error: error.message })}\n\n`);
    } finally {
      res.write('data: {"type":"done"}\n\n');
      res.end();
    }
  } catch (error) {
    console.error('Error querying session:', error);
    if (!res.headersSent) {
      res.status(500).json({ error: error.message });
    }
  }
});

/**
 * Abort a session
 * POST /session/:sessionId/abort
 */
app.post('/session/:sessionId/abort', async (req, res) => {
  try {
    const { sessionId } = req.params;
    const session = sessions.get(sessionId);
    
    if (!session) {
      return res.status(404).json({ error: 'Session not found' });
    }

    await session.abort();
    res.json({ success: true });
  } catch (error) {
    console.error('Error aborting session:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * Close and remove a session
 * DELETE /session/:sessionId
 */
app.delete('/session/:sessionId', (req, res) => {
  try {
    const { sessionId } = req.params;
    const existed = sessions.delete(sessionId);
    
    res.json({ success: existed });
  } catch (error) {
    console.error('Error deleting session:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * List all active sessions
 * GET /sessions
 */
app.get('/sessions', (req, res) => {
  const sessionList = Array.from(sessions.keys()).map(id => ({
    sessionId: id,
    model: sessions.get(id)._selectedModel
  }));
  
  res.json({ sessions: sessionList });
});

// Start server
app.listen(PORT, () => {
  console.log(`Copilot Bridge Server listening on http://localhost:${PORT}`);
  console.log(`Node version: ${process.version}`);
  console.log(`Ready to accept connections from Python backend`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, closing sessions...');
  sessions.clear();
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('SIGINT received, closing sessions...');
  sessions.clear();
  process.exit(0);
});
