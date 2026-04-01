"use client";

import { Canvas } from "@react-three/fiber";
import { Suspense } from "react";
import NeuralNetwork from "./NeuralNetwork";
import AmbientDust from "./AmbientDust";
import CameraRig from "./CameraRig";
import Effects from "./Effects";

interface Scene3DProps {
  scrollProgress: number;
  mouseX: number;
  mouseY: number;
}

function SceneContent({ scrollProgress, mouseX, mouseY }: Scene3DProps) {
  return (
    <>
      {/* Minimal lighting -- nodes are self-lit via MeshBasicMaterial */}
      <ambientLight intensity={0.05} />

      {/* Warm key light from above-right */}
      <pointLight position={[8, 6, 4]} intensity={0.6} color="#E09F3E" />
      {/* Cool fill from below-left */}
      <pointLight position={[-6, -4, 6]} intensity={0.2} color="#4F46E5" />

      <CameraRig
        scrollProgress={scrollProgress}
        mouseX={mouseX}
        mouseY={mouseY}
      />

      <NeuralNetwork scrollProgress={scrollProgress} />
      <AmbientDust />

      <Effects />
    </>
  );
}

export default function Scene3D({ scrollProgress, mouseX, mouseY }: Scene3DProps) {
  return (
    <div className="fixed inset-0 z-0" aria-hidden="true">
      <Canvas
        camera={{ position: [0, 2, 14], fov: 45, near: 0.1, far: 100 }}
        dpr={[1, 1.5]}
        gl={{
          antialias: true,
          alpha: false,
          powerPreference: "high-performance",
        }}
        style={{ background: "#030014" }}
        fallback={
          <div className="w-full h-full bg-[#030014]" />
        }
      >
        <Suspense fallback={null}>
          <SceneContent
            scrollProgress={scrollProgress}
            mouseX={mouseX}
            mouseY={mouseY}
          />
        </Suspense>
      </Canvas>
    </div>
  );
}
