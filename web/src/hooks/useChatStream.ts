import { useCallback, useRef, useState } from "react";
import { useWebSocket, type ConnectionStatus } from "./useWebSocket";

export interface ToolCallInfo {
  name: string;
  args: Record<string, unknown>;
  preview?: string;
  status: "running" | "done";
}

export interface StreamingState {
  isStreaming: boolean;
  streamContent: string;
  activeTools: ToolCallInfo[];
}

interface OnCompletePayload {
  content: string;
  toolCalls: ToolCallInfo[];
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
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  onCompleteRef.current = onComplete;
  onErrorRef.current = onError;

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
          bufferRef.current += msg.content as string;
          scheduleFlush();
          break;

        case "tool_call": {
          const tool: ToolCallInfo = {
            name: msg.name as string,
            args: (msg.args as Record<string, unknown>) ?? {},
            status: "running",
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "tool_result": {
          const name = msg.name as string;
          const preview = msg.preview as string;
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === name && t.status === "running"
              ? { ...t, status: "done" as const, preview }
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
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "specialist_done": {
          const teamName = `team:${msg.name as string}`;
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === teamName && t.status === "running"
              ? { ...t, status: "done" as const }
              : t,
          );
          scheduleFlush();
          break;
        }

        case "done": {
          const content = (msg.content as string) || bufferRef.current;
          const tools = [...toolsRef.current];
          resetStream();
          onCompleteRef.current({ content, toolCalls: tools });
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
