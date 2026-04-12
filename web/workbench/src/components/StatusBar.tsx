export function StatusBar({ message }: { message: string }) {
  return (
    <footer className="status-bar">
      <span className="status-text">{message}</span>
    </footer>
  );
}
