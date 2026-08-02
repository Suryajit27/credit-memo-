import { randomUUID } from "node:crypto";
import { Router, type IRouter } from "express";
import multer from "multer";
import {
  ApproveMemoSectionBody,
  FinalizeMemoBody,
  GetIndexerStatusQueryParams,
  GetMemoStatusQueryParams,
  RegenerateMemoSectionBody,
  SearchDocumentsBody,
} from "@workspace/api-zod";

const router: IRouter = Router();
const upload = multer({ storage: multer.memoryStorage() });

const AZURE_FUNC_API_URL = (process.env.AZURE_FUNC_API_URL || "http://localhost:7071/api").replace(/\/$/, "");
const AZURE_FUNC_API_KEY = process.env.AZURE_FUNC_API_KEY || "";

function withFunctionKey(headers: Record<string, string> = {}): Record<string, string> {
  if (!AZURE_FUNC_API_KEY) return headers;
  return { ...headers, "x-functions-key": AZURE_FUNC_API_KEY };
}

function toBlobPart(buffer: Buffer): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(new ArrayBuffer(buffer.byteLength));
  bytes.set(buffer);
  return bytes;
}

// Helper to forward JSON POST requests
async function forwardJsonPost(endpoint: string, body: any, res: any) {
  try {
    const response = await fetch(`${AZURE_FUNC_API_URL}${endpoint}`, {
      method: "POST",
      headers: withFunctionKey({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => null);
    if (data) {
      res.status(response.status).json(data);
    } else {
      res.status(response.status).end();
    }
  } catch (error: any) {
    res.status(500).json({ error: error.message || "Failed to reach Azure Functions API" });
  }
}

// Helper to forward GET requests
async function forwardGet(endpoint: string, res: any) {
  try {
    const response = await fetch(`${AZURE_FUNC_API_URL}${endpoint}`, { method: "GET", headers: withFunctionKey() });
    const data = await response.json().catch(() => null);
    if (data) {
      res.status(response.status).json(data);
    } else {
      res.status(response.status).end();
    }
  } catch (error: any) {
    res.status(500).json({ error: error.message || "Failed to reach Azure Functions API" });
  }
}

// 1. Unified Classify and Upload for UI Intake
router.post("/classify-upload", upload.single("file"), async (req, res) => {
  try {
    const file = req.file;
    if (!file) {
      res.status(400).json({ error: "No file uploaded" });
      return;
    }

    const requestId = String(req.body.requestId || `IDX-${randomUUID().slice(0, 8).toUpperCase()}`);
    const isZip = file.originalname.toLowerCase().endsWith(".zip");

    if (isZip) {
      // Forward ZIP to Python /classify endpoint
      const formData = new FormData();
      formData.append("file", new Blob([toBlobPart(file.buffer)], { type: file.mimetype }), file.originalname);

      const response = await fetch(`${AZURE_FUNC_API_URL}/classify`, {
        method: "POST",
        headers: withFunctionKey(),
        body: formData as any,
      });

      const data: any = await response.json().catch(() => null);
      if (!response.ok) {
        res.status(response.status).json(data || { error: "Backend classify failed" });
        return;
      }
      if (!data) {
        res.status(502).json({ error: "Empty response from backend classify" });
        return;
      }

      // Map Python ClassificationResponse -> React UI ClassifiedFile array
      const documents = (data.documents || []).map((doc: any) => ({
        name: doc.fileName,
        type: doc.documentType || "Unclassified",
        confidence: doc.confidence ? Math.round(doc.confidence * 100) : 0,
        pages: 1,
        state: doc.status === "Failed" ? "review" : "classified",
      }));

      res.json({ requestId, documents });
    } else {
      // Single file classification via /classify-file
      const formData = new FormData();
      formData.append("file", new Blob([toBlobPart(file.buffer)], { type: file.mimetype }), file.originalname);
      formData.append("fileName", file.originalname);

      const classifyRes = await fetch(`${AZURE_FUNC_API_URL}/classify-file`, {
        method: "POST",
        headers: withFunctionKey(),
        body: formData as any,
      });

      const classifyData: any = await classifyRes.json().catch(() => null);
      if (!classifyRes.ok) {
        res.status(classifyRes.status).json(classifyData || { error: "Backend classify-file failed" });
        return;
      }
      if (!classifyData) {
        res.status(502).json({ error: "Empty response from backend classify-file" });
        return;
      }

      const docType = classifyData.documentType || "Unclassified";
      const confidence = classifyData.confidence || 0;

      // Make secondary API call to /upload endpoint to persist in Azure Blob & Cosmos DB
      const uploadFormData = new FormData();
      uploadFormData.append("file", new Blob([toBlobPart(file.buffer)], { type: file.mimetype }), file.originalname);
      uploadFormData.append("fileName", file.originalname);
      uploadFormData.append("requestId", requestId);
      uploadFormData.append("documentType", docType);
      uploadFormData.append("confidence", String(confidence));
      uploadFormData.append("status", classifyData.status || "Success");

      await fetch(`${AZURE_FUNC_API_URL}/upload`, {
        method: "POST",
        headers: withFunctionKey(),
        body: uploadFormData as any,
      }).catch(() => null);

      const documents = [
        {
          name: classifyData.fileName || file.originalname,
          type: docType,
          confidence: Math.round(confidence * 100),
          pages: 1,
          state: classifyData.status === "Failed" ? "review" : "classified",
        },
      ];

      res.json({ requestId, documents });
    }
  } catch (error: any) {
    res.status(500).json({ error: error.message || "Classification failed" });
  }
});

// 2. Direct Single File Classify
router.post("/classify-file", upload.single("file"), async (req, res) => {
  try {
    const file = req.file;
    if (!file) {
      res.status(400).json({ error: "No file uploaded" });
      return;
    }
    const formData = new FormData();
    formData.append("file", new Blob([toBlobPart(file.buffer)], { type: file.mimetype }), file.originalname);
    formData.append("fileName", String(req.body.fileName || file.originalname));

    const response = await fetch(`${AZURE_FUNC_API_URL}/classify-file`, {
      method: "POST",
      headers: withFunctionKey(),
      body: formData as any,
    });
    const data = await response.json().catch(() => null);
    if (data) {
      res.status(response.status).json(data);
    } else {
      res.status(response.status).end();
    }
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// 3. Direct Upload Endpoint
router.post("/upload", upload.single("file"), async (req, res) => {
  try {
    const file = req.file;
    if (!file) {
      res.status(400).json({ error: "No file uploaded" });
      return;
    }
    const formData = new FormData();
    formData.append("file", new Blob([toBlobPart(file.buffer)], { type: file.mimetype }), file.originalname);
    formData.append("fileName", String(req.body.fileName || file.originalname));
    formData.append("requestId", String(req.body.requestId || ""));
    formData.append("documentType", String(req.body.documentType || "UNCLASSIFIED"));
    formData.append("confidence", String(req.body.confidence || "0"));
    formData.append("status", String(req.body.status || "Success"));

    const response = await fetch(`${AZURE_FUNC_API_URL}/upload`, {
      method: "POST",
      headers: withFunctionKey(),
      body: formData as any,
    });
    const data = await response.json().catch(() => null);
    if (data) {
      res.status(response.status).json(data);
    } else {
      res.status(response.status).end();
    }
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// 4. Trigger Indexing
router.post("/trigger-indexing", (req, res) => {
  forwardJsonPost("/trigger-indexing", req.body, res);
});

// 5. Indexer Status
router.get("/indexer-status", (req, res) => {
  const parsed = GetIndexerStatusQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId is required" });
    return;
  }
  forwardGet(`/indexer-status?requestId=${encodeURIComponent(parsed.data.requestId)}`, res);
});

// 6. Search
router.post("/search", (req, res) => {
  const parsed = SearchDocumentsBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "query and requestId are required" });
    return;
  }
  forwardJsonPost("/search", req.body, res);
});

// 7. Credit Memo Status
router.get("/memo/status", (req, res) => {
  const parsed = GetMemoStatusQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId is required" });
    return;
  }
  forwardGet(`/memo/status?requestId=${encodeURIComponent(parsed.data.requestId)}`, res);
});

// 8. Approve Memo Section
router.post("/memo/section/approve", (req, res) => {
  const parsed = ApproveMemoSectionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId and sectionName are required" });
    return;
  }
  forwardJsonPost("/memo/section/approve", req.body, res);
});

// 9. Regenerate Memo Section
router.post("/memo/section/regenerate", (req, res) => {
  const parsed = RegenerateMemoSectionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId and sectionName are required" });
    return;
  }
  forwardJsonPost("/memo/section/regenerate", req.body, res);
});

// 9b. Stream Regenerate Memo Section (SSE)
router.post("/memo/section/regenerate/stream", async (req, res) => {
  const parsed = RegenerateMemoSectionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId and sectionName are required" });
    return;
  }

  const requestId = String(req.body.requestId || "");
  const sectionName = String(req.body.sectionName || "");
  const reviewerNotes = typeof req.body.reviewerNotes === "string" ? req.body.reviewerNotes : "";

  try {
    const response = await fetch(`${AZURE_FUNC_API_URL}/memo/section/regenerate/stream`, {
      method: "POST",
      headers: withFunctionKey({ "Content-Type": "application/json" }),
      body: JSON.stringify({ requestId, sectionName, reviewerNotes }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: "Backend error" }));
      res.status(response.status).json(err);
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
      "Transfer-Encoding": "chunked",
    });

    res.flushHeaders?.();
    res.socket?.setNoDelay(true);
    res.socket?.setKeepAlive(true, 10000);
    res.write(": regen-stream-open\n\n");

    if (response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          res.write(decoder.decode(value, { stream: true }));
          (res as any).flush?.();
        }
      } finally {
        reader.releaseLock();
      }
    }

    res.end();
  } catch (error: any) {
    if (!res.headersSent) {
      res.status(500).json({ error: error.message || "Failed to stream section regenerate" });
    } else {
      res.end();
    }
  }
});

// 10. Finalize Memo
router.post("/memo/finalize", (req, res) => {
  const parsed = FinalizeMemoBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "requestId and approver are required" });
    return;
  }
  forwardJsonPost("/memo/finalize", req.body, res);
});

// 11. Stream Memo Drafting (SSE)
router.post("/memo/stream", async (req, res) => {
  const requestId = String(req.body.requestId || req.query.requestId || "");
  if (!requestId) {
    res.status(400).json({ error: "requestId is required" });
    return;
  }
  try {
    const response = await fetch(`${AZURE_FUNC_API_URL}/memo/stream`, {
      method: "POST",
      headers: withFunctionKey({ "Content-Type": "application/json" }),
      body: JSON.stringify({ requestId }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: "Backend error" }));
      res.status(response.status).json(err);
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
      "Transfer-Encoding": "chunked",
    });

    res.flushHeaders?.();
    res.socket?.setNoDelay(true);
    res.socket?.setKeepAlive(true, 10000);
    res.write(": memo-stream-open\n\n");

    if (response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          res.write(decoder.decode(value, { stream: true }));
          (res as any).flush?.();
        }
      } finally {
        reader.releaseLock();
      }
    }
    res.end();
  } catch (error: any) {
    if (!res.headersSent) {
      res.status(500).json({ error: error.message || "Failed to stream memo" });
    } else {
      res.end();
    }
  }
});

// 12. Stream Request-Scoped Document Chat (SSE)
router.post("/chat/stream", async (req, res) => {
  const requestId = String(req.body.requestId || req.query.requestId || "");
  const message = String(req.body.message || "");
  const history = Array.isArray(req.body.history) ? req.body.history : [];

  if (!requestId || !message) {
    res.status(400).json({ error: "requestId and message are required" });
    return;
  }

  try {
    const response = await fetch(`${AZURE_FUNC_API_URL}/chat/stream`, {
      method: "POST",
      headers: withFunctionKey({ "Content-Type": "application/json" }),
      body: JSON.stringify({ requestId, message, history }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: "Backend error" }));
      res.status(response.status).json(err);
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      "Transfer-Encoding": "chunked",
    });

    res.flushHeaders?.();
    res.socket?.setNoDelay(true);
    res.socket?.setKeepAlive(true, 10000);
    res.write(": chat-stream-open\n\n");

    if (response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          res.write(decoder.decode(value, { stream: true }));
          (res as any).flush?.();
        }
      } finally {
        reader.releaseLock();
      }
    }

    res.end();
  } catch (error: any) {
    if (!res.headersSent) {
      res.status(500).json({ error: error.message || "Failed to stream chat" });
    } else {
      res.end();
    }
  }
});

export default router;
