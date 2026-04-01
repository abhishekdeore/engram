"use client";

import dynamic from "next/dynamic";
import Nav from "@/components/layout/Nav";
import Hero from "@/components/sections/Hero";
import Problem from "@/components/sections/Problem";
import HowItWorks from "@/components/sections/HowItWorks";
import Architecture from "@/components/sections/Architecture";
import CTA from "@/components/sections/CTA";
import { useScrollProgress } from "@/lib/hooks/useScrollProgress";
import { useCursorPosition } from "@/lib/hooks/useCursorPosition";

const Scene3D = dynamic(() => import("@/components/canvas/Scene3D"), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 z-0 bg-[#030014]" aria-hidden="true" />
  ),
});

export default function Home() {
  const scrollProgress = useScrollProgress();
  const cursor = useCursorPosition();

  return (
    <>
      <Nav />

      <Scene3D
        scrollProgress={scrollProgress}
        mouseX={cursor.x}
        mouseY={cursor.y}
      />

      <main className="relative z-10">
        <Hero />

        {/* Fade into readable sections */}
        <div className="bg-gradient-to-b from-transparent via-[rgba(3,0,20,0.8)] to-[rgba(3,0,20,0.92)]">
          <Problem />
        </div>

        {/* Graph visible through */}
        <HowItWorks />

        {/* Solid for readability */}
        <div className="bg-[rgba(3,0,20,0.9)]">
          <Architecture />
        </div>

        {/* Final: graph visible */}
        <CTA />
      </main>
    </>
  );
}
