"use client";

import { useRef, useMemo, useEffect } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface GraphEdgeProps {
  start: [number, number, number];
  end: [number, number, number];
  color?: string;
  opacity?: number;
  animated?: boolean;
  visible?: boolean;
}

export default function GraphEdge({
  start,
  end,
  color = "#7C6AF0",
  opacity = 0.4,
  animated = true,
  visible = true,
}: GraphEdgeProps) {
  const lineRef = useRef<THREE.Line | null>(null);

  const { geometry, material } = useMemo(() => {
    const s = new THREE.Vector3(...start);
    const e = new THREE.Vector3(...end);
    const mid = new THREE.Vector3().addVectors(s, e).multiplyScalar(0.5);

    const dist = s.distanceTo(e);
    mid.y += dist * 0.15;

    const curve = new THREE.QuadraticBezierCurve3(s, mid, e);
    const points = curve.getPoints(50);
    const geometry = new THREE.BufferGeometry().setFromPoints(points);

    const material = new THREE.LineBasicMaterial({
      color: new THREE.Color(color),
      transparent: true,
      opacity,
      depthWrite: false,
    });

    return { geometry, material };
  }, [start, end, color, opacity]);

  useEffect(() => {
    if (!lineRef.current) {
      const line = new THREE.Line(geometry, material);
      lineRef.current = line;
    }
    return () => {
      geometry.dispose();
      material.dispose();
    };
  }, [geometry, material]);

  useFrame((state) => {
    if (!lineRef.current || !animated) return;
    // Subtle opacity pulse
    const mat = lineRef.current.material as THREE.LineBasicMaterial;
    mat.opacity =
      opacity * (0.7 + Math.sin(state.clock.elapsedTime * 1.5) * 0.3);
  });

  if (!visible) return null;

  return <primitive object={lineRef.current ?? new THREE.Line(geometry, material)} />;
}
