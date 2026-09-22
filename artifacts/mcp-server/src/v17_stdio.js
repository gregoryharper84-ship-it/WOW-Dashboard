import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createV17McpServer } from "./v17_server.js";

const server = createV17McpServer();
const transport = new StdioServerTransport();
await server.connect(transport);
