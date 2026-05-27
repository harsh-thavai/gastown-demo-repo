# MEGA UI PROMPT — Professional Design System for Gas Town

## Core Philosophy
Build like Stripe, Linear, and Vercel. Not like a generic AI demo.
Every component must have a reason to exist. Every pixel must earn its place.

---

## Typography
- Font: system UI stack via `font-sans` — NEVER use just Inter alone without weights
- Heading scale: 4xl/3xl/2xl/xl — bold, tight tracking (`tracking-tight`)
- Body: `text-sm text-muted-foreground` — subdued, readable
- Labels: `text-xs font-medium uppercase tracking-wider text-muted-foreground`

## Color System — 95% Monochrome, 5% Accent
```
Background:   bg-white dark:bg-slate-950
Surface:      bg-slate-50 dark:bg-slate-900
Border:       border-slate-200 dark:border-slate-800
Text primary: text-slate-900 dark:text-slate-100
Text muted:   text-slate-500 dark:text-slate-400
Accent:       ONE color — violet-600 / blue-600 / emerald-600
Danger:       text-red-600 / bg-red-50
Success:      text-emerald-600 / bg-emerald-50
```
NEVER use: rainbow gradients, neon colors, multiple accent colors

## Spacing — Double What You Think You Need
```
Page padding:    p-6 md:p-8 lg:p-10
Section gap:     space-y-8 or gap-8
Card padding:    p-6
Form gap:        space-y-4
Icon + text:     gap-2
Button padding:  px-4 py-2 (default), px-6 py-3 (large)
```

## Layout Patterns

### Dashboard Shell
```tsx
<div className="flex h-screen bg-white dark:bg-slate-950">
  <aside className="w-60 border-r border-slate-200 dark:border-slate-800 flex flex-col p-4">
    <div className="text-lg font-bold mb-8">AppName</div>
    <nav className="space-y-1 flex-1">
      {navItems.map(item => (
        <a key={item.href} href={item.href}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm
                     hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors
                     text-slate-600 dark:text-slate-400
                     aria-[current]:bg-violet-50 aria-[current]:text-violet-600">
          <item.icon className="h-4 w-4" />
          {item.label}
        </a>
      ))}
    </nav>
  </aside>
  <div className="flex-1 flex flex-col overflow-hidden">
    <header className="h-14 border-b border-slate-200 dark:border-slate-800
                       flex items-center px-6 gap-4">
      <h1 className="text-base font-semibold">Page Title</h1>
    </header>
    <main className="flex-1 overflow-auto p-6">{children}</main>
  </div>
</div>
```

### Stats Row
```tsx
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
  {[
    { label: "Total Revenue", value: "$48,295", delta: "+12.5%", up: true },
    { label: "Active Users", value: "2,841", delta: "+3.2%", up: true },
    { label: "Conversion", value: "3.6%", delta: "-0.4%", up: false },
    { label: "Avg Order", value: "$124", delta: "+8.1%", up: true },
  ].map(s => (
    <div key={s.label} className="rounded-xl border border-slate-200 dark:border-slate-800
                                   bg-white dark:bg-slate-900 p-6">
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">{s.label}</p>
      <p className="mt-2 text-3xl font-bold tracking-tight text-slate-900 dark:text-white">{s.value}</p>
      <p className={`mt-1 text-xs font-medium ${s.up ? 'text-emerald-600' : 'text-red-500'}`}>
        {s.up ? '↑' : '↓'} {s.delta} vs last month
      </p>
    </div>
  ))}
</div>
```

### Form Component
```tsx
<form className="space-y-5 max-w-sm">
  <div className="space-y-1.5">
    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
      Email address
    </label>
    <input
      type="email"
      placeholder="you@example.com"
      className="w-full rounded-lg border border-slate-200 dark:border-slate-700
                 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm
                 placeholder:text-slate-400
                 focus:outline-none focus:ring-2 focus:ring-violet-500/20
                 focus:border-violet-500 transition-all"
    />
  </div>
  <button className="w-full rounded-lg bg-violet-600 text-white px-4 py-2.5
                     text-sm font-semibold hover:bg-violet-700
                     active:scale-[0.98] transition-all">
    Continue with Email
  </button>
</form>
```

## Component Recipes

### Buttons
```
Primary:   bg-violet-600 text-white hover:bg-violet-700 active:scale-[0.98]
Secondary: border border-slate-200 bg-white hover:bg-slate-50
Ghost:     hover:bg-slate-100 text-slate-600
Danger:    bg-red-600 text-white hover:bg-red-700
All:       rounded-lg px-4 py-2 text-sm font-medium transition-all
```

### Cards
```
Base:      rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900
Hover:     hover:border-violet-300 hover:shadow-sm transition-all cursor-pointer
Active:    ring-2 ring-violet-500/20 border-violet-400
```

### Badges
```
Default:   bg-slate-100 text-slate-700 rounded-full px-2.5 py-0.5 text-xs font-medium
Success:   bg-emerald-100 text-emerald-700
Warning:   bg-amber-100 text-amber-700
Danger:    bg-red-100 text-red-700
Blue:      bg-blue-100 text-blue-700
```

### Empty State
```tsx
<div className="flex flex-col items-center justify-center py-16 text-center">
  <div className="rounded-full bg-slate-100 dark:bg-slate-800 p-4 mb-4">
    <svg className="h-6 w-6 text-slate-400" .../>
  </div>
  <h3 className="font-semibold text-slate-900 dark:text-white">Nothing here yet</h3>
  <p className="mt-1 text-sm text-slate-500 max-w-xs">
    Create your first item to get started.
  </p>
  <button className="mt-4 rounded-lg bg-violet-600 text-white px-4 py-2 text-sm font-medium">
    Create Item
  </button>
</div>
```

## Realistic Mock Data (always hardcode, NEVER use empty arrays or undefined)
```tsx
const users = [
  { id: 1, name: "Sarah Chen", email: "sarah@acme.com", role: "Admin", status: "Active", joined: "Jan 2025" },
  { id: 2, name: "Marcus Webb", email: "marcus@acme.com", role: "Editor", status: "Active", joined: "Feb 2025" },
  { id: 3, name: "Priya Patel", email: "priya@acme.com", role: "Viewer", status: "Inactive", joined: "Mar 2025" },
];
const revenue = [12400, 18200, 15800, 22100, 19500, 28400, 31200];
```

## Rules — ALWAYS DO
- Hardcode realistic mock data — names, numbers, dates
- Use dark mode classes everywhere (`dark:bg-slate-950 dark:text-white`)
- Add hover states to ALL clickable elements
- Include navigation (sidebar or top nav) with active state
- Add loading skeleton: `<div className="animate-pulse h-4 bg-slate-200 rounded w-3/4" />`
- Use `transition-all` or `transition-colors` on interactive elements
- Mobile responsive: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4`

## Anti-Patterns — NEVER DO
- Rainbow gradients on page backgrounds
- `shadow-2xl` on cards (use `shadow-sm` max)
- Hard-coded hex colors (`text-[#1a2b3c]`) — use Tailwind scale
- Generic hero text ("Welcome to App", "Get started today")
- Empty arrays for lists — always have 3-5 realistic items
- Placeholder text (`Lorem ipsum`, `Coming soon`)
- `<img>` without alt text
- Forms with no validation states
- Pages with just one centered card and nothing else
