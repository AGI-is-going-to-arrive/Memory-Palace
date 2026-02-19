import React from 'react';
import { motion } from 'framer-motion';
import clsx from 'clsx';

const GlassCard = ({ children, className, hoverEffect = true, as: Component = motion.div, ...props }) => {
  return (
    <Component
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 10 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className={clsx(
        "glass-card relative overflow-hidden",
        hoverEffect && "hover:shadow-[0_12px_40px_rgba(179,133,79,0.15)] hover:-translate-y-[2px]",
        className
      )}
      {...props}
    >
      {/* Internal shine effect */}
      <div className="absolute inset-0 bg-gradient-to-tr from-white/20 via-transparent to-transparent opacity-0 transition-opacity duration-300 hover:opacity-100 pointer-events-none" />

      {children}
    </Component>
  );
};

export default GlassCard;
