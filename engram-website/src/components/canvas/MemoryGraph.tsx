"use client";

import { useMemo } from "react";
import GraphNode from "./GraphNode";
import GraphEdge from "./GraphEdge";

interface MemoryGraphProps {
  scrollProgress: number;
}

// Provider colors from the design system
const COLORS = {
  chatgpt: "#10A37F",
  claude: "#D97706",
  gemini: "#4285F4",
  grok: "#FFFFFF",
  copilot: "#6366F1",
  user: "#F0EDE8",
  message_user: "#22D3EE",
  message_assistant: "#F59E0B",
  segment: "#8B5CF6",
  accent: "#7C6AF0",
};

// The graph data: modeled after Engram's real Neo4j data structure
// User -> Conversation -> Message chain, with Segments
const GRAPH_DATA = {
  nodes: [
    // Central user node
    { id: "user", type: "user" as const, pos: [0, 0, 0] as [number, number, number], color: COLORS.user },

    // ChatGPT conversation cluster (left)
    { id: "conv-chatgpt", type: "conversation" as const, pos: [-5, 1.5, -2] as [number, number, number], color: COLORS.chatgpt },
    { id: "msg-c1", type: "message" as const, pos: [-6.5, 2.5, -1] as [number, number, number], color: COLORS.message_user },
    { id: "msg-c2", type: "message" as const, pos: [-5.5, 3, -3] as [number, number, number], color: COLORS.message_assistant },
    { id: "msg-c3", type: "message" as const, pos: [-4, 2.8, -1.5] as [number, number, number], color: COLORS.message_user },
    { id: "seg-c1", type: "segment" as const, pos: [-5.2, 3.5, -2] as [number, number, number], color: COLORS.segment },

    // Claude conversation cluster (right)
    { id: "conv-claude", type: "conversation" as const, pos: [5, 0.5, -1] as [number, number, number], color: COLORS.claude },
    { id: "msg-cl1", type: "message" as const, pos: [6, 1.5, 0] as [number, number, number], color: COLORS.message_assistant },
    { id: "msg-cl2", type: "message" as const, pos: [5.8, 2, -2] as [number, number, number], color: COLORS.message_user },
    { id: "msg-cl3", type: "message" as const, pos: [4.5, 1.8, -0.5] as [number, number, number], color: COLORS.message_assistant },
    { id: "seg-cl1", type: "segment" as const, pos: [5.5, 2.5, -1] as [number, number, number], color: COLORS.segment },

    // Gemini conversation cluster (top)
    { id: "conv-gemini", type: "conversation" as const, pos: [0, 4, -3] as [number, number, number], color: COLORS.gemini },
    { id: "msg-g1", type: "message" as const, pos: [-1, 5, -4] as [number, number, number], color: COLORS.message_user },
    { id: "msg-g2", type: "message" as const, pos: [1, 5.2, -2.5] as [number, number, number], color: COLORS.message_assistant },
    { id: "seg-g1", type: "segment" as const, pos: [0, 5.5, -3.2] as [number, number, number], color: COLORS.segment },

    // Grok conversation cluster (lower left)
    { id: "conv-grok", type: "conversation" as const, pos: [-3, -2.5, -1] as [number, number, number], color: COLORS.grok },
    { id: "msg-gr1", type: "message" as const, pos: [-4, -1.5, 0] as [number, number, number], color: COLORS.message_user },
    { id: "msg-gr2", type: "message" as const, pos: [-2.5, -1.8, -2] as [number, number, number], color: COLORS.message_assistant },

    // Copilot conversation cluster (lower right)
    { id: "conv-copilot", type: "conversation" as const, pos: [3.5, -2, -2] as [number, number, number], color: COLORS.copilot },
    { id: "msg-cp1", type: "message" as const, pos: [4.5, -1, -1.5] as [number, number, number], color: COLORS.message_user },
    { id: "msg-cp2", type: "message" as const, pos: [3, -1.2, -3] as [number, number, number], color: COLORS.message_assistant },
  ],

  // Edges: modeled after real Engram relationships
  edges: [
    // HAS_CONVERSATION (User -> Conversation)
    { from: "user", to: "conv-chatgpt", color: COLORS.chatgpt, phase: 0.35 },
    { from: "user", to: "conv-claude", color: COLORS.claude, phase: 0.38 },
    { from: "user", to: "conv-gemini", color: COLORS.gemini, phase: 0.42 },
    { from: "user", to: "conv-grok", color: COLORS.grok, phase: 0.45 },
    { from: "user", to: "conv-copilot", color: COLORS.copilot, phase: 0.48 },

    // HAS_MESSAGE (Conversation -> Messages)
    { from: "conv-chatgpt", to: "msg-c1", color: COLORS.chatgpt, phase: 0.3 },
    { from: "conv-chatgpt", to: "msg-c2", color: COLORS.chatgpt, phase: 0.3 },
    { from: "conv-chatgpt", to: "msg-c3", color: COLORS.chatgpt, phase: 0.3 },
    { from: "conv-claude", to: "msg-cl1", color: COLORS.claude, phase: 0.3 },
    { from: "conv-claude", to: "msg-cl2", color: COLORS.claude, phase: 0.3 },
    { from: "conv-claude", to: "msg-cl3", color: COLORS.claude, phase: 0.3 },
    { from: "conv-gemini", to: "msg-g1", color: COLORS.gemini, phase: 0.3 },
    { from: "conv-gemini", to: "msg-g2", color: COLORS.gemini, phase: 0.3 },
    { from: "conv-grok", to: "msg-gr1", color: COLORS.grok, phase: 0.3 },
    { from: "conv-grok", to: "msg-gr2", color: COLORS.grok, phase: 0.3 },
    { from: "conv-copilot", to: "msg-cp1", color: COLORS.copilot, phase: 0.3 },
    { from: "conv-copilot", to: "msg-cp2", color: COLORS.copilot, phase: 0.3 },

    // NEXT_MESSAGE chains
    { from: "msg-c1", to: "msg-c2", color: "#555", phase: 0.3 },
    { from: "msg-c2", to: "msg-c3", color: "#555", phase: 0.3 },
    { from: "msg-cl1", to: "msg-cl2", color: "#555", phase: 0.3 },
    { from: "msg-cl2", to: "msg-cl3", color: "#555", phase: 0.3 },

    // HAS_SEGMENT
    { from: "conv-chatgpt", to: "seg-c1", color: COLORS.segment, phase: 0.4 },
    { from: "conv-claude", to: "seg-cl1", color: COLORS.segment, phase: 0.4 },
    { from: "conv-gemini", to: "seg-g1", color: COLORS.segment, phase: 0.4 },

    // CONTAINS_MESSAGE (Segment -> Messages)
    { from: "seg-c1", to: "msg-c1", color: COLORS.segment, phase: 0.45 },
    { from: "seg-c1", to: "msg-c2", color: COLORS.segment, phase: 0.45 },
    { from: "seg-c1", to: "msg-c3", color: COLORS.segment, phase: 0.45 },
    { from: "seg-cl1", to: "msg-cl1", color: COLORS.segment, phase: 0.45 },
    { from: "seg-cl1", to: "msg-cl2", color: COLORS.segment, phase: 0.45 },
  ],
};

export default function MemoryGraph({ scrollProgress }: MemoryGraphProps) {
  const nodeMap = useMemo(() => {
    const map = new Map<string, (typeof GRAPH_DATA.nodes)[0]>();
    for (const node of GRAPH_DATA.nodes) {
      map.set(node.id, node);
    }
    return map;
  }, []);

  // Connections between clusters appear as scroll progresses past their phase threshold
  // Before the threshold, only intra-cluster edges are visible
  // The "connection moment" is the scroll range 0.30 - 0.55

  return (
    <group>
      {/* Render all nodes -- they're always visible but start isolated */}
      {GRAPH_DATA.nodes.map((node) => (
        <GraphNode
          key={node.id}
          position={node.pos}
          type={node.type}
          color={node.color}
          connected={scrollProgress > 0.35}
          floatSpeed={node.type === "user" ? 0.3 : 0.8 + Math.random() * 0.5}
          floatIntensity={node.type === "user" ? 0.1 : 0.2 + Math.random() * 0.2}
          pulseSpeed={0.5 + Math.random() * 0.5}
        />
      ))}

      {/* Render edges -- they appear based on scroll progress */}
      {GRAPH_DATA.edges.map((edge, i) => {
        const fromNode = nodeMap.get(edge.from);
        const toNode = nodeMap.get(edge.to);
        if (!fromNode || !toNode) return null;

        // Intra-cluster edges (HAS_MESSAGE, NEXT_MESSAGE) visible earlier
        // Cross-cluster edges (HAS_CONVERSATION from user) appear at their phase
        const isVisible = scrollProgress > edge.phase;

        return (
          <GraphEdge
            key={`${edge.from}-${edge.to}-${i}`}
            start={fromNode.pos}
            end={toNode.pos}
            color={edge.color}
            opacity={isVisible ? 0.5 : 0}
            visible={isVisible}
            animated={isVisible}
          />
        );
      })}
    </group>
  );
}
