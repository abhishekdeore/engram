"use client";

import { motion } from "motion/react";

const STATS = [
  { value: "242", label: "Tests passing" },
  { value: "<32ms", label: "Write latency" },
  { value: "11", label: "API endpoints" },
  { value: "5", label: "Provider adapters" },
  { value: "1536", label: "Embedding dims" },
  { value: "0", label: "LLMs in the pipeline" },
];

const STACK = [
  { name: "FastAPI", role: "API layer" },
  { name: "Neo4j", role: "Graph DB" },
  { name: "OpenAI", role: "Embeddings only" },
  { name: "MCP", role: "LLM protocol" },
  { name: "JWT", role: "Auth" },
  { name: "Redis", role: "Cache" },
];

export default function Architecture() {
  return (
    <section id="resources" className="relative z-10 py-32 px-6">
      <div className="max-w-5xl mx-auto">
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="text-xs font-mono tracking-[0.25em] uppercase text-[#5D5A73] mb-4"
        >
          For engineers
        </motion.p>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="text-3xl sm:text-4xl md:text-5xl font-semibold tracking-[-0.02em] max-w-2xl leading-[1.1] mb-16"
        >
          Production-grade.{" "}
          <span className="text-[#5D5A73]">Not a weekend demo.</span>
        </motion.h2>

        {/* Stats */}
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mb-14">
          {STATS.map((stat, i) => (
            <motion.div
              key={stat.label}
              initial={{ opacity: 0, y: 15 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.05 }}
              className="text-center py-4 px-2 rounded-lg border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]"
            >
              <div className="text-xl sm:text-2xl font-semibold text-[#E09F3E]">
                {stat.value}
              </div>
              <div className="text-[10px] text-[#5D5A73] mt-1 uppercase tracking-wider">
                {stat.label}
              </div>
            </motion.div>
          ))}
        </div>

        {/* Stack row */}
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.2 }}
          className="flex flex-wrap justify-center gap-6 mb-14"
        >
          {STACK.map((tech) => (
            <div key={tech.name} className="text-center">
              <span className="text-sm font-medium text-[#E2DFD6]">
                {tech.name}
              </span>
              <span className="text-xs text-[#5D5A73] ml-1.5">{tech.role}</span>
            </div>
          ))}
        </motion.div>

        {/* Code snippet terminal */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: 0.3 }}
          className="rounded-xl border border-[rgba(255,255,255,0.07)] overflow-hidden shadow-[0_4px_24px_rgba(0,0,0,0.4)]"
        >
          <div className="flex items-center gap-2 px-4 py-3 bg-[#1C1B22] border-b border-[rgba(255,255,255,0.06)]">
            <div className="flex items-center gap-1.5">
              <div className="w-[10px] h-[10px] rounded-full bg-[#FF5F57] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
              <div className="w-[10px] h-[10px] rounded-full bg-[#FEBC2E] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
              <div className="w-[10px] h-[10px] rounded-full bg-[#28C840] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
            </div>
            <span className="ml-2 text-[10px] font-mono text-[#6B6880]">
              engram -- semantic retrieval
            </span>
          </div>
          <pre className="bg-[#13121A] p-4 text-xs sm:text-sm font-mono text-[#8B8A94] overflow-x-auto leading-relaxed">
            <code>{`curl -X POST /memory/query \\
  -H "Authorization: Bearer <token>" \\
  -d '{
    "query": "quantum computing error correction",
    "topK": 5,
    "tokenBudget": 4000
  }'

# Returns verbatim content from any provider
# ChatGPT conversation from March 5th
# Claude follow-up from March 8th
# Ranked by cosine similarity (> 0.70)`}</code>
          </pre>
        </motion.div>
      </div>
    </section>
  );
}
