import "./globals.css";

export const metadata = {
  title: "Horus Security Lab | عين حورس",
  description: "Isolated security testing workspace with explicit scope controls and human approvals.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ar" dir="rtl"><body>{children}</body></html>;
}
