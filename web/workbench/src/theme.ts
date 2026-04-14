/**
 * RoadGen3D Design Tokens
 * 统一设计语言配置 - 基于 Ant Design 5.0
 */

import type { ThemeConfig } from 'antd';

// 颜色系统 - 轻快明亮风格
export const COLORS = {
  // 主色 - 道路/城市主题蓝
  primary: '#3B82F6',
  primaryHover: '#2563EB',
  primaryActive: '#1D4ED8',
  primaryBg: '#EFF6FF',

  // 评估维度颜色
  walkability: '#3B82F6',  // 步行性 - 蓝
  safety: '#F97316',       // 安全性 - 橙 (改为橙色更友好)
  beauty: '#10B981',       // 美观度 - 绿
  overall: '#8B5CF6',      // 综合 - 紫

  // 状态色
  success: '#10B981',
  warning: '#F59E0B',
  error: '#EF4444',
  info: '#3B82F6',

  // 中性色
  textPrimary: '#111827',
  textSecondary: '#6B7280',
  textMuted: '#9CA3AF',
  bgPrimary: '#F9FAFB',
  bgSecondary: '#F3F4F6',
  bgCard: '#FFFFFF',
  border: '#E5E7EB',
  borderStrong: '#D1D5DB',
};

// 排版系统
export const TYPOGRAPHY = {
  fontFamily: '"Inter", "PingFang SC", "Noto Sans SC", system-ui, -apple-system, sans-serif',
  fontFamilyMono: '"SF Mono", "Menlo", "Monaco", "Cascadia Code", monospace',
  fontSizeXS: 12,
  fontSizeSM: 13,
  fontSizeMD: 14,
  fontSizeLG: 16,
  fontSizeXL: 20,
  lineHeight: 1.5,
};

// 间距系统
export const SPACING = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

// 圆角
export const RADIUS = {
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
  full: 9999,
};

// 阴影
export const SHADOWS = {
  sm: '0 1px 2px rgba(0, 0, 0, 0.05)',
  md: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1)',
  lg: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1)',
};

// Ant Design 主题配置
export const antdTheme: ThemeConfig = {
  token: {
    // 基础 Token
    colorPrimary: COLORS.primary,
    colorSuccess: COLORS.success,
    colorWarning: COLORS.warning,
    colorError: COLORS.error,
    colorInfo: COLORS.info,

    // 排版
    fontFamily: TYPOGRAPHY.fontFamily,
    fontSize: TYPOGRAPHY.fontSizeMD,
    lineHeight: TYPOGRAPHY.lineHeight,

    // 圆角
    borderRadius: RADIUS.md,
    borderRadiusLG: RADIUS.lg,

    // 间距
    paddingXS: SPACING.xs,
    paddingSM: SPACING.sm,
    padding: SPACING.md,
    paddingLG: SPACING.lg,

    // 阴影
    boxShadow: SHADOWS.md,
    boxShadowSecondary: SHADOWS.sm,

    // 线条颜色
    colorBorder: COLORS.border,
    colorBorderSecondary: COLORS.borderStrong,

    // 背景色
    colorBgLayout: COLORS.bgPrimary,
    colorBgContainer: COLORS.bgCard,
    colorBgElevated: COLORS.bgCard,
    colorBgSpotlight: COLORS.textPrimary,
  },
  components: {
    Button: {
      borderRadius: RADIUS.full,
      fontWeight: 500,
      controlHeight: 36,
    },
    Card: {
      borderRadiusLG: RADIUS.lg,
      boxShadow: SHADOWS.sm,
    },
    Steps: {
      borderRadius: RADIUS.full,
    },
    Statistic: {
      contentFontSize: 24,
    },
    Tag: {
      borderRadius: RADIUS.sm,
    },
  },
};
