import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocket, type ConnectionStatus } from "./useWebSocket";

export interface ToolCallInfo {
  name: string;
  args: Record<string, unknown>;
  preview?: string;
  status: "running" | "done" | "error";
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  error?: string;
}

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cost?: number;
  model?: string;
}

export interface StreamingState {
  isStreaming: boolean;
  streamContent: string;
  activeTools: ToolCallInfo[];
}

interface OnCompletePayload {
  content: string;
  toolCalls: ToolCallInfo[];
  usage?: UsageInfo | null;
  latency_ms?: number;
}

interface UseChatStreamOptions {
  onComplete: (payload: OnCompletePayload) => void;
  onError: (message: string) => void;
  enabled?: boolean;
}

interface UseChatStreamReturn {
  sendMessage: (content: string, sessionId: string) => void;
  cancelGeneration: () => void;
  streamingState: StreamingState;
  connectionStatus: ConnectionStatus;
}

export function useChatStream({
  onComplete,
  onError,
  enabled = true,
}: UseChatStreamOptions): UseChatStreamReturn {
  const [streamingState, setStreamingState] = useState<StreamingState>({
    isStreaming: false,
    streamContent: "",
    activeTools: [],
  });

  // Buffer tokens and flush via rAF to avoid excessive re-renders
  const bufferRef = useRef("");
  const rafRef = useRef<number>(0);
  const toolsRef = useRef<ToolCallInfo[]>([]);
  const usageRef = useRef<UsageInfo | null>(null);
  const sendTimeRef = useRef<number>(0);
  const firstTokenTimeRef = useRef<number>(0);
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
  }, [onComplete, onError]);

  const flushBuffer = useCallback(() => {
    const content = bufferRef.current;
    const tools = [...toolsRef.current];
    setStreamingState({ isStreaming: true, streamContent: content, activeTools: tools });
    rafRef.current = 0;
  }, []);

  const scheduleFlush = useCallback(() => {
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(flushBuffer);
    }
  }, [flushBuffer]);

  const resetStream = useCallback(() => {
    bufferRef.current = "";
    toolsRef.current = [];
    usageRef.current = null;
    firstTokenTimeRef.current = 0;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    setStreamingState({ isStreaming: false, streamContent: "", activeTools: [] });
  }, []);

  const handleMessage = useCallback(
    (data: unknown) => {
      const msg = data as Record<string, unknown>;
      const type = msg.type as string;

      switch (type) {
        case "token":
          if (!firstTokenTimeRef.current) {
            firstTokenTimeRef.current = Date.now();
          }
          bufferRef.current += msg.content as string;
          scheduleFlush();
          break;

        case "tool_call": {
          const tool: ToolCallInfo = {
            name: msg.name as string,
            args: (msg.args as Record<string, unknown>) ?? {},
            status: "running",
            started_at: Date.now(),
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "tool_result": {
          const name = msg.name as string;
          const preview = msg.preview as string;
          const error = msg.error as string | undefined;
          const now = Date.now();
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === name && t.status === "running"
              ? {
                  ...t,
                  status: error ? ("error" as const) : ("done" as const),
                  preview,
                  error,
                  completed_at: now,
                  duration_ms: t.started_at ? now - t.started_at : undefined,
                }
              : t,
          );
          scheduleFlush();
          break;
        }

        case "specialist_start": {
          const tool: ToolCallInfo = {
            name: `team:${msg.name as string}`,
            args: { task: msg.task as string },
            status: "running",
            started_at: Date.now(),
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "specialist_done": {
          const teamName = `team:${msg.name as string}`;
          const now = Date.now();
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === teamName && t.status === "running"
              ? {
                  ...t,
                  status: "done" as const,
                  completed_at: now,
                  duration_ms: t.started_at ? now - t.started_at : undefined,
                }
              : t,
          );
          scheduleFlush();
          break;
        }

        case "thinking":
          // Agent reasoning — captured for future display
          break;

        case "usage": {
          // Token usage event from backend
          usageRef.current = {
            input_tokens: msg.input_tokens as number | undefined,
            output_tokens: msg.output_tokens as number | undefined,
            total_tokens: msg.total_tokens as number | undefined,
            cost: msg.cost as number | undefined,
            model: msg.model as string | undefined,
          };
          break;
        }

        case "done": {
          const content = (msg.content as string) || bufferRef.current;
          const tools = [...toolsRef.current];
          // Capture usage from done event payload or from earlier usage event
          const msgUsage = msg.usage as Record<string, unknown> | undefined;
          const usage: UsageInfo | null = msgUsage
            ? {
                input_tokens: msgUsage.input_tokens as number | undefined,
                output_tokens: msgUsage.output_tokens as number | undefined,
                total_tokens: msgUsage.total_tokens as number | undefined,
                cost: msgUsage.cost as number | undefined,
                model: msgUsage.model as string | undefined,
              }
            : usageRef.current;
          const latency_ms = firstTokenTimeRef.current && sendTimeRef.current
            ? firstTokenTimeRef.current - sendTimeRef.current
            : undefined;
          resetStream();
          onCompleteRef.current({ content, toolCalls: tools, usage, latency_ms });
          break;
        }

        case "error":
          resetStream();
          onErrorRef.current(msg.message as string);
          break;

        case "cancelled":
          resetStream();
          break;
      }
    },
    [scheduleFlush, resetStream],
  );

  const { send, status: connectionStatus } = useWebSocket({
    onMessage: handleMessage,
    enabled,
  });

  const sendMessage = useCallback(
    (content: string, sessionId: string) => {
      bufferRef.current = "";
      toolsRef.current = [];
      usageRef.current = null;
      firstTokenTimeRef.current = 0;
      sendTimeRef.current = Date.now();
      setStreamingState({ isStreaming: true, streamContent: "", activeTools: [] });
      send({ type: "message", content, session_id: sessionId });
    },
    [send],
  );

  const cancelGeneration = useCallback(() => {
    send({ type: "cancel" });
  }, [send]);

  return { sendMessage, cancelGeneration, streamingState, connectionStatus };
}
