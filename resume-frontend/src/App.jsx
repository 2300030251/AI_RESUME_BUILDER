import { useMemo, useState } from "react";

const API_BASE = "http://127.0.0.1:5000";

function App() {
  const [mode, setMode] = useState("login");
  const [userId, setUserId] = useState(null);

  const [registerForm, setRegisterForm] = useState({
    name: "",
    email: "",
    password: "",
  });

  const [loginForm, setLoginForm] = useState({
    email: "",
    password: "",
  });

  const [resumeForm, setResumeForm] = useState({
    template: "software_engineer",
    description: "",
  });

  const [resumeText, setResumeText] = useState("");
  const [status, setStatus] = useState({ loading: false, message: "", error: false });

  const templates = useMemo(
    () => [
      { value: "software_engineer", label: "Software Engineer" },
      { value: "data_analyst", label: "Data Analyst" },
      { value: "devops_engineer", label: "DevOps Engineer" },
      { value: "product_manager", label: "Product Manager" },
      { value: "ui_ux_designer", label: "UI/UX Designer" },
    ],
    []
  );

  const setLoading = (loading) => setStatus((prev) => ({ ...prev, loading }));
  const setMessage = (message, error = false) => setStatus({ loading: false, message, error });

  const registerUser = async (event) => {
    event.preventDefault();
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(registerForm),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || "Registration failed");
      }

      setMessage(data.message || "User Registered Successfully");
      setMode("login");
      setRegisterForm({ name: "", email: "", password: "" });
    } catch (error) {
      setMessage(error.message, true);
    }
  };

  const loginUser = async (event) => {
    event.preventDefault();
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });

      const data = await response.json();
      if (!response.ok || !data.user_id) {
        throw new Error(data.message || "Login failed");
      }

      setUserId(data.user_id);
      setMessage("Login successful");
      setLoginForm({ email: "", password: "" });
    } catch (error) {
      setMessage(error.message, true);
    }
  };

  const generateResume = async (event) => {
    event.preventDefault();
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/generate_resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          template: resumeForm.template,
          description: resumeForm.description,
        }),
      });

      const data = await response.json();
      if (!response.ok || !data.resume) {
        throw new Error(data.message || "Resume generation failed");
      }

      setResumeText(data.resume);
      setMessage("Resume generated successfully");
    } catch (error) {
      setMessage(error.message, true);
    }
  };

  return (
    <div className="app-shell">
      <div className="card">
        <h1>AI Resume Builder</h1>
        <p className="subtitle">Frontend rebuilt and connected to Flask backend.</p>

        {status.message ? (
          <div className={`status ${status.error ? "error" : "success"}`}>{status.message}</div>
        ) : null}

        {!userId ? (
          <>
            <div className="tabs">
              <button
                type="button"
                className={mode === "login" ? "active" : ""}
                onClick={() => setMode("login")}
              >
                Login
              </button>
              <button
                type="button"
                className={mode === "register" ? "active" : ""}
                onClick={() => setMode("register")}
              >
                Register
              </button>
            </div>

            {mode === "login" ? (
              <form onSubmit={loginUser}>
                <label>Email</label>
                <input
                  type="email"
                  value={loginForm.email}
                  onChange={(event) =>
                    setLoginForm((prev) => ({ ...prev, email: event.target.value }))
                  }
                  required
                />

                <label>Password</label>
                <input
                  type="password"
                  value={loginForm.password}
                  onChange={(event) =>
                    setLoginForm((prev) => ({ ...prev, password: event.target.value }))
                  }
                  required
                />

                <button type="submit" disabled={status.loading}>
                  {status.loading ? "Please wait..." : "Login"}
                </button>
              </form>
            ) : (
              <form onSubmit={registerUser}>
                <label>Name</label>
                <input
                  type="text"
                  value={registerForm.name}
                  onChange={(event) =>
                    setRegisterForm((prev) => ({ ...prev, name: event.target.value }))
                  }
                  required
                />

                <label>Email</label>
                <input
                  type="email"
                  value={registerForm.email}
                  onChange={(event) =>
                    setRegisterForm((prev) => ({ ...prev, email: event.target.value }))
                  }
                  required
                />

                <label>Password</label>
                <input
                  type="password"
                  value={registerForm.password}
                  onChange={(event) =>
                    setRegisterForm((prev) => ({ ...prev, password: event.target.value }))
                  }
                  required
                />

                <button type="submit" disabled={status.loading}>
                  {status.loading ? "Please wait..." : "Create Account"}
                </button>
              </form>
            )}
          </>
        ) : (
          <>
            <div className="logged-in">Logged in user id: {userId}</div>

            <form onSubmit={generateResume}>
              <label>Template</label>
              <select
                value={resumeForm.template}
                onChange={(event) =>
                  setResumeForm((prev) => ({ ...prev, template: event.target.value }))
                }
              >
                {templates.map((template) => (
                  <option key={template.value} value={template.value}>
                    {template.label}
                  </option>
                ))}
              </select>

              <label>Professional Summary / Details</label>
              <textarea
                rows="8"
                value={resumeForm.description}
                onChange={(event) =>
                  setResumeForm((prev) => ({ ...prev, description: event.target.value }))
                }
                placeholder="Write your profile summary, skills, and experience details..."
                required
              />

              <button type="submit" disabled={status.loading}>
                {status.loading ? "Generating..." : "Generate Resume"}
              </button>
            </form>

            {resumeText ? (
              <div className="result">
                <h2>Generated Resume</h2>
                <pre>{resumeText}</pre>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export default App;
