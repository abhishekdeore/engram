"use client";

import { motion } from "motion/react";

const STEPS = [
  {
    number: "01",
    title: "Save",
    description:
      'Tell any LLM to save the conversation. "Remember this." That\'s it.',
    terminalTitle: "engram -- write",
    terminal: [
      { type: "user" as const, text: 'User  "Save this to my memory"' },
      { type: "sys" as const, text: "POST /memory/write" },
      { type: "sys" as const, text: "202 Accepted" },
      { type: "sys" as const, text: "Stored verbatim. No AI in the pipeline." },
    ],
  },
  {
    number: "02",
    title: "Store",
    description:
      "Your conversation is stored word for word in a Neo4j graph. Segmented, embedded, and indexed.",
    terminalTitle: "engram -- neo4j",
    terminal: [
      { type: "sys" as const, text: "Conversation node created" },
      { type: "sys" as const, text: "12 messages stored verbatim" },
      { type: "sys" as const, text: "1 segment embedded (1536 dims)" },
      { type: "sys" as const, text: "Vector index updated" },
    ],
  },
  {
    number: "03",
    title: "Retrieve",
    description:
      "Ask any other LLM about it. Engram finds the exact conversation and returns it unchanged.",
    terminalTitle: "engram -- query",
    terminal: [
      { type: "user" as const, text: 'User  "Remember the quantum computing research?"' },
      { type: "sys" as const, text: 'POST /memory/query  "quantum computing"' },
      { type: "sys" as const, text: "3 segments matched (cosine > 0.82)" },
      { type: "sys" as const, text: "Verbatim content returned." },
    ],
  },
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="relative z-10 py-32 px-6">
      <div className="max-w-5xl mx-auto">
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="text-xs font-mono tracking-[0.25em] uppercase text-[#5D5A73] mb-4"
        >
          How it works
        </motion.p>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="text-3xl sm:text-4xl md:text-5xl font-semibold tracking-[-0.02em] max-w-2xl leading-[1.1] mb-20"
        >
          Three steps.{" "}
          <span className="text-[#E09F3E]">Zero hallucination.</span>
        </motion.h2>

        <div className="space-y-16">
          {STEPS.map((step) => (
            <motion.div
              key={step.number}
              initial={{ opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{
                type: "spring",
                stiffness: 50,
                damping: 20,
              }}
              className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start"
            >
              <div>
                <span className="text-xs font-mono text-[#E09F3E] opacity-60 mb-2 block">
                  {step.number}
                </span>
                <h3 className="text-2xl sm:text-3xl font-semibold mb-3 tracking-[-0.01em]">
                  {step.title}
                </h3>
                <p className="text-[#7E7B93] text-sm leading-relaxed max-w-md">
                  {step.description}
                </p>
              </div>

              {/* Terminal */}
              <div className="rounded-xl border border-[rgba(255,255,255,0.07)] overflow-hidden shadow-[0_4px_24px_rgba(0,0,0,0.4)]">
                <div className="flex items-center gap-2 px-4 py-3 bg-[#1C1B22] border-b border-[rgba(255,255,255,0.06)]">
                  <div className="flex items-center gap-1.5">
                    <div className="w-[10px] h-[10px] rounded-full bg-[#FF5F57] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
                    <div className="w-[10px] h-[10px] rounded-full bg-[#FEBC2E] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
                    <div className="w-[10px] h-[10px] rounded-full bg-[#28C840] shadow-[inset_0_-1px_1px_rgba(0,0,0,0.2)]" />
                  </div>
                  <span className="ml-2 text-[10px] font-mono text-[#7E7B93]">
                    {step.terminalTitle}
                  </span>
                </div>
                <div className="bg-[#13121A] p-4 space-y-1.5">
                  {step.terminal.map((line, j) => (
                    <motion.div
                      key={j}
                      initial={{ opacity: 0, x: -8 }}
                      whileInView={{ opacity: 1, x: 0 }}
                      viewport={{ once: true }}
                      transition={{ delay: 0.2 + j * 0.12 }}
                      className="font-mono text-xs sm:text-sm leading-relaxed"
                    >
                      <span className="text-[#5D5A73] mr-1.5 select-none">
                        {line.type === "user" ? ">" : "$"}
                      </span>
                      {line.type === "user" ? (
                        <span className="text-[#E09F3E]">{line.text}</span>
                      ) : (
                        <span className="text-[#8B8A94]">{line.text}</span>
                      )}
                    </motion.div>
                  ))}
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
