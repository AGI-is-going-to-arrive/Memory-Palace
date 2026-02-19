import React from 'react';
import { motion } from 'framer-motion';

const FluidBackground = () => {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden bg-[#fdfbf7]">
      {/* Base Gradient Layer - Static for performance */}
      <div className="absolute inset-0 bg-gradient-to-br from-[#fdfbf7] via-[#f7f2ea] to-[#efe5d6] opacity-80" />

      {/* Animated Blobs - Optimized: Reduced count and complexity */}
      <motion.div
        className="absolute -top-[10%] -left-[10%] w-[40vw] h-[40vw] rounded-full bg-[#d4af37]/10 blur-[80px]"
        animate={{
          x: [0, 50, 0],
          y: [0, 30, 0],
          scale: [1, 1.1, 1],
        }}
        transition={{
          duration: 25, // Slower for less CPU usage
          repeat: Infinity,
          ease: "easeInOut"
        }}
        style={{ willChange: "transform" }} // Hardware acceleration hint
      />

      <motion.div
        className="absolute top-[30%] right-[10%] w-[35vw] h-[35vw] rounded-full bg-[#e6dccf]/30 blur-[60px]"
        animate={{
          x: [0, -40, 0],
          y: [0, 50, 0],
          scale: [1, 1.05, 1],
        }}
        transition={{
          duration: 30,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 2
        }}
        style={{ willChange: "transform" }}
      />

       {/* Subtle Grid Overlay - Static */}
       <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
            backgroundImage: `linear-gradient(#d4af37 1px, transparent 1px), linear-gradient(90deg, #d4af37 1px, transparent 1px)`,
            backgroundSize: '40px 40px'
        }}
       />
    </div>
  );
};

export default FluidBackground;
