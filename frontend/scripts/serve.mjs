import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, isAbsolute, join, normalize, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const preferredPort = Number(process.env.PORT || 5173);
const host = process.env.HOST || "127.0.0.1";

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon",
};

function resolveRequestPath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const clean = normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const requested = resolve(join(root, clean));
  const relativePath = relative(root, requested);

  if (relativePath.startsWith("..") || isAbsolute(relativePath)) {
    return null;
  }

  if (existsSync(requested) && statSync(requested).isFile()) {
    return requested;
  }

  return join(root, "index.html");
}

function listen(port) {
  const server = createServer((request, response) => {
    const filePath = resolveRequestPath(request.url || "/");

    if (!filePath || !existsSync(filePath)) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }

    response.writeHead(200, {
      "Content-Type": mimeTypes[extname(filePath)] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    createReadStream(filePath).pipe(response);
  });

  server.on("error", (error) => {
    if (error.code === "EADDRINUSE" && port < preferredPort + 20) {
      listen(port + 1);
      return;
    }
    throw error;
  });

  server.listen(port, host, () => {
    console.log(`AgentHub frontend: http://${host}:${port}`);
  });
}

listen(preferredPort);
