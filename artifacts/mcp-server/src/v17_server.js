import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

import { V17_OPERATIONS, V17_TOOLS } from "./v17_contract.js";
import { errorPayload, invokeV17Operation } from "./v17_backend.js";
import {
  DEFAULT_OAUTH_IDENTITY_SCOPES,
  buildOAuthChallenge,
  hasOperationPermission,
  requiredPermission,
} from "./v17_oauth.js";

function resultEnvelope(payload, isError = false, meta = null) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    ...(meta ? { _meta: meta } : {}),
    ...(isError ? { isError: true } : {}),
  };
}

export function getV17ToolDescriptors(authMode = "shared-token", identityScopes = DEFAULT_OAUTH_IDENTITY_SCOPES) {
  if (authMode !== "oauth") return V17_TOOLS;
  const securitySchemes = [{ type: "oauth2", scopes: [...identityScopes] }];
  return V17_TOOLS.map((entry) => ({ ...entry, securitySchemes }));
}

function permissionDeniedEnvelope(operationName, oauthConfig) {
  const permission = requiredPermission(operationName);
  const description = `Authenticated identity lacks required V17 permission '${permission}'.`;
  const challenge = buildOAuthChallenge(oauthConfig, {
    error: "insufficient_scope",
    description,
  });
  return resultEnvelope(
    {
      ok: false,
      gateway_status: "MCP_OAUTH_PERMISSION_DENIED",
      message: description,
      required_permission: permission,
      can_execute: false,
    },
    true,
    { "mcp/www_authenticate": [challenge] },
  );
}

export function createV17McpServer(options = {}) {
  const authMode = String(options.authMode || process.env.WOW_MCP_AUTH_MODE || "shared-token").toLowerCase();
  const identityScopes = options.oauthConfig?.identityScopes || DEFAULT_OAUTH_IDENTITY_SCOPES;
  const server = new Server(
    { name: "wow-v17-mcp", version: "1.0.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: getV17ToolDescriptors(authMode, identityScopes),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const operationName = request.params.name;
    const args = request.params.arguments || {};

    if (!V17_OPERATIONS[operationName]) {
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

    if (authMode === "oauth") {
      const authContext = options.auth?.current;
      if (!authContext || !hasOperationPermission(authContext, operationName)) {
        return permissionDeniedEnvelope(operationName, options.oauthConfig);
      }
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
