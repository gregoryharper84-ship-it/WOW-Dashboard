import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

import { V17_OPERATIONS, V17_TOOLS } from "./v17_contract.js";
import { errorPayload, invokeV17Operation } from "./v17_backend.js";

function resultEnvelope(payload, isError = false) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    ...(isError ? { isError: true } : {}),
  };
}

export function getV17VisibleTools({ healthOnly = false } = {}) {
  return healthOnly ? V17_TOOLS.filter((entry) => entry.name === "getWowV17BackendHealth") : V17_TOOLS;
}

export function createV17McpServer(options = {}) {
  const visibleTools = getV17VisibleTools(options);
  const visibleNames = new Set(visibleTools.map((entry) => entry.name));
  const server = new Server(
    { name: "wow-v17-mcp", version: "1.0.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: visibleTools }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const operationName = request.params.name;
    const args = request.params.arguments || {};

    if (!visibleNames.has(operationName) || !V17_OPERATIONS[operationName]) {
      return resultEnvelope(
        {
          ok: false,
          gateway_status: "MCP_TOOL_NOT_REGISTERED",
          message: `Tool '${operationName}' is not part of the governed WOW V17 MCP surface.`,
          can_execute: false,
        },
        true,
      );
    }

    try {
      const payload = await invokeV17Operation(operationName, args, options.backend || {});
      return resultEnvelope(payload, false);
    } catch (error) {
      return resultEnvelope(errorPayload(error, operationName), true);
    }
  });

  return server;
}
