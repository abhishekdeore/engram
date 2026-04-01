"use client";

import { useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Float } from "@react-three/drei";
import * as THREE from "three";

type NodeType = "user" | "conversation" | "message" | "segment";

interface GraphNodeProps {
  position: [number, number, number];
  type: NodeType;
  color?: string;
  label?: string;
  scale?: number;
  floatSpeed?: number;
  floatIntensity?: number;
  pulseSpeed?: number;
  connected?: boolean;
}

const NODE_CONFIG: Record<
  NodeType,
  {
    geometry: "icosahedron" | "roundedBox" | "sphere" | "torus";
    baseScale: number;
    emissiveIntensity: number;
  }
> = {
  user: { geometry: "icosahedron", baseScale: 0.5, emissiveIntensity: 0.6 },
  conversation: { geometry: "roundedBox", baseScale: 0.35, emissiveIntensity: 0.4 },
  message: { geometry: "sphere", baseScale: 0.15, emissiveIntensity: 0.5 },
  segment: { geometry: "torus", baseScale: 0.25, emissiveIntensity: 0.3 },
};

export default function GraphNode({
  position,
  type,
  color = "#7C6AF0",
  scale = 1,
  floatSpeed = 1,
  floatIntensity = 0.3,
  pulseSpeed = 1,
  connected = false,
}: GraphNodeProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const config = NODE_CONFIG[type];
  const targetEmissive = useRef(config.emissiveIntensity);

  useFrame((state) => {
    if (!meshRef.current) return;

    // Gentle pulse
    const pulse =
      Math.sin(state.clock.elapsedTime * pulseSpeed * 0.8) * 0.1 + 1;
    const hoverScale = hovered ? 1.3 : 1;
    const connectedScale = connected ? 1.1 : 1;
    const s = config.baseScale * scale * pulse * hoverScale * connectedScale;
    meshRef.current.scale.setScalar(
      THREE.MathUtils.lerp(meshRef.current.scale.x, s, 0.08)
    );

    // Emissive pulse
    const mat = meshRef.current.material as THREE.MeshStandardMaterial;
    if (mat.emissiveIntensity !== undefined) {
      const targetI = hovered
        ? config.emissiveIntensity * 2
        : connected
        ? config.emissiveIntensity * 1.4
        : config.emissiveIntensity;
      targetEmissive.current = targetI;
      mat.emissiveIntensity = THREE.MathUtils.lerp(
        mat.emissiveIntensity,
        targetEmissive.current +
          Math.sin(state.clock.elapsedTime * pulseSpeed) * 0.15,
        0.05
      );
    }

    // Glow outer shell
    if (glowRef.current) {
      const gs = s * 1.6;
      glowRef.current.scale.setScalar(
        THREE.MathUtils.lerp(glowRef.current.scale.x, gs, 0.06)
      );
      const glowMat = glowRef.current.material as THREE.MeshBasicMaterial;
      glowMat.opacity = THREE.MathUtils.lerp(
        glowMat.opacity,
        hovered ? 0.15 : connected ? 0.08 : 0.04,
        0.05
      );
    }
  });

  const renderGeometry = () => {
    switch (config.geometry) {
      case "icosahedron":
        return <icosahedronGeometry args={[1, 1]} />;
      case "roundedBox":
        return <boxGeometry args={[1, 1, 1]} />;
      case "sphere":
        return <sphereGeometry args={[1, 24, 24]} />;
      case "torus":
        return <torusGeometry args={[1, 0.3, 16, 32]} />;
    }
  };

  return (
    <Float speed={floatSpeed} rotationIntensity={0.15} floatIntensity={floatIntensity}>
      <group position={position}>
        {/* Main node */}
        <mesh
          ref={meshRef}
          onPointerOver={() => setHovered(true)}
          onPointerOut={() => setHovered(false)}
        >
          {renderGeometry()}
          <meshStandardMaterial
            color={color}
            emissive={color}
            emissiveIntensity={config.emissiveIntensity}
            roughness={0.3}
            metalness={0.1}
            transparent
            opacity={0.9}
          />
        </mesh>

        {/* Glow shell */}
        <mesh ref={glowRef}>
          <sphereGeometry args={[1, 16, 16]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0.04}
            depthWrite={false}
            side={THREE.BackSide}
          />
        </mesh>
      </group>
    </Float>
  );
}
