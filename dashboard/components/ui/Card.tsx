/**
 * ui/Card.tsx — Primitiva card QuantLuna
 * Variante: default | elevated | glass | danger | highlight
 * Subcomponente: Card.Header, Card.Title, Card.Body, Card.Footer
 */
import React from 'react';

type Variant = 'default' | 'elevated' | 'glass' | 'danger' | 'highlight';

interface CardProps {
  variant?:   Variant;
  className?: string;
  children:   React.ReactNode;
  onClick?:   () => void;
  style?:     React.CSSProperties;
}

const variantClass: Record<Variant, string> = {
  default:   'ql-card',
  elevated:  'ql-card bg-[var(--bg-elevated)]',
  glass:     'ql-card ql-card-glass',
  danger:    'ql-card ql-card-danger',
  highlight: 'ql-card ql-card-highlight',
};

export function Card({ variant = 'default', className = '', children, onClick, style }: CardProps) {
  return (
    <div
      className={`${variantClass[variant]} p-5 ${
        onClick ? 'cursor-pointer' : ''
      } ${className}`}
      onClick={onClick}
      style={style}
    >
      {children}
    </div>
  );
}

Card.Header = function CardHeader({
  children, className = '',
}: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`flex items-center justify-between mb-4 ${className}`}>
      {children}
    </div>
  );
};

Card.Title = function CardTitle({
  children, className = '',
}: { children: React.ReactNode; className?: string }) {
  return (
    <h2 className={`text-white font-semibold text-base leading-snug ${className}`}>
      {children}
    </h2>
  );
};

Card.Body = function CardBody({
  children, className = '',
}: { children: React.ReactNode; className?: string }) {
  return <div className={className}>{children}</div>;
};

Card.Footer = function CardFooter({
  children, className = '',
}: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`mt-4 pt-4 border-t border-[var(--border)] ${className}`}>
      {children}
    </div>
  );
};
