"use client";

import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

interface CameraRigProps {
  scrollProgress: number;
  mouseX?: number;
  mouseY?: number;
}

// Slower, more cinematic keyframes
const KEYFRAMES = [
  { at: 0.0, pos: [0, 2, 14] as const },
  { at: 0.2, pos: [-2, 1.5, 12] as const },
  { at: 0.4, pos: [1, 0.5, 8] as const },
  { at: 0.6, pos: [-1, 1, 10] as const },
  { at: 0.8, pos: [2, 2, 12] as const },
  { at: 1.0, pos: [0, 3, 15] as const },
];

function interpolateKeyframes(progress: number): [number, number, number] {
  let a = KEYFRAMES[0];
  let b = KEYFRAMES[1];
  for (let i = 0; i < KEYFRAMES.length - 1; i++) {
    if (progress >= KEYFRAMES[i].at && progress <= KEYFRAMES[i + 1].at) {
      a = KEYFRAMES[i];
      b = KEYFRAMES[i + 1];
      break;
    }
  }

  const range = b.at - a.at;
  const t = range === 0 ? 0 : (progress - a.at) / range;
  const eased = t * t * (3 - 2 * t);

  return [
    THREE.MathUtils.lerp(a.pos[0], b.pos[0], eased),
    THREE.MathUtils.lerp(a.pos[1], b.pos[1], eased),
    THREE.MathUtils.lerp(a.pos[2], b.pos[2], eased),
  ];
}

export default function CameraRig({
  scrollProgress,
  mouseX = 0,
  mouseY = 0,
}: CameraRigProps) {
  const target = useRef(new THREE.Vector3(0, 2, 14));
  const { camera } = useThree();

  useFrame(() => {
    const [x, y, z] = interpolateKeyframes(scrollProgress);

    // Subtle mouse parallax (much less aggressive)
    target.current.set(x + mouseX * 0.3, y + mouseY * 0.15, z);

    // Very smooth damping
    camera.position.lerp(target.current, 0.02);
    camera.lookAt(0, 0, 0);
  });

  return null;
}
