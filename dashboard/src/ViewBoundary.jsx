import React from "react";

/**
 * Keeps a failing view from unmounting the whole console. Without it, one exception in the 3D view
 * or the map (a WebGL context that will not start, a bad frame) blanks the page, and with it the
 * stage rail and a rover drive's E-STOP. The failure is shown in place and the view can be retried.
 */
export default class ViewBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error(`The ${this.props.name} failed:`, error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="view-boundary" role="alert">
        <p>
          The {this.props.name} stopped: {error?.message ?? String(error)}. The rest of the console is
          unaffected.
        </p>
        <button type="button" className="btn btn--ghost" onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    );
  }
}
