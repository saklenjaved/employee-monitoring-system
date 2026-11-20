# Technical Architecture Document (converted)

Source: Windows_Agent_Technical_Architecture_Document (1).docx. Text converted from the docx. Table cells are separated by ' | '. Figures are images in the source; their captions are kept.

TECHNICAL ARCHITECTURE DOCUMENT
Windows Desktop Monitoring Agent
Employee Monitoring & Workforce Productivity System
Document Item
 | Details
 | Document scope
 | Separate technical architecture for the Windows desktop agent
 | Product
 | Employee Monitoring & Workforce Productivity System
 | Agent platform
 | Native Windows Agent — initial release
 | Agent language
 | Python
 | Backend integration
 | Django REST Framework over HTTPS
 | Status
 | Working draft for technical review
 | Basis
 | Product scope and architecture decisions in the supplied requirements document
 | 
CONFIDENTIAL — WORKING DRAFT

1. WHAT — What is the Windows Agent?
The Windows Agent is a lightweight desktop application installed on an employee’s Windows computer. It runs during the configured monitoring period, collects approved work-activity signals, captures screenshots when permitted by the organization’s policy, and securely sends the collected information to the Django backend.
1.1 Responsibilities
Identify the registered employee device and maintain its connection with the backend.
Collect the active application and focused-window information.
Collect supported website activity using the agreed browser-tracking method.
Collect activity signals to calculate active and idle time. It must not record actual keystrokes or typed text.
Capture periodic screenshots only when the organization’s screenshot policy allows it.
Synchronize monitoring events and screenshot metadata with the backend.
Show the employee that the agent is installed and indicate its monitoring/connection status.
1.2 Out of scope
Continuous screen recording, webcam monitoring and microphone recording.
Actual keystroke logging or collection of typed text.
Payroll or salary calculation, domain blocking, and advanced USB/device monitoring.
Advanced AI predictions or recommendations.
2. WHY — Why is the Agent required?
The web application cannot reliably observe desktop application usage, foreground-window changes, local activity signals or screenshots by itself. The Windows Agent provides these device-level signals so the platform can build an employee workday timeline and display employee, team and organization-level monitoring information.
Provide visibility into application and supported website usage.
Calculate active and idle time from permitted activity signals.
Capture policy-controlled screenshots as visual records.
Send consistent device activity data to the central platform for dashboards and reports.
3. WHO — Who interacts with the Agent?
Actor / Component
 | Responsibility
 | Employee
 | Installs the agent, signs in or completes device registration, sees agent/monitoring status and follows the organization’s monitoring notice.
 | Windows Agent
 | Collects permitted signals, applies local capture checks, queues/retries data as designed and communicates with the backend.
 | Django backend
 | Authenticates the device, validates events, applies organization policy and permissions, and stores accepted records.
 | Organization Admin
 | Configures monitoring policy and manages employee/device access within the organization.
 | Manager
 | Views permitted monitoring information for assigned teams only.
 | Super Admin
 | Manages platform-level organizations and access according to the approved platform policy.
 | Operations / Support
 | Packages releases, monitors agent health and handles approved troubleshooting.
 | 
4. WHERE — Where does each part run?
Location
 | Technology / Component
 | Purpose
 | Employee Windows device
 | Python native Windows Agent
 | Collects application/window activity, activity signals and policy-approved screenshots.
 | Employee Windows device
 | Windows APIs / approved Python libraries
 | Reads foreground-window and input-idle signals; captures the desktop image when allowed.
 | Communication channel
 | HTTPS / TLS
 | Secure agent-to-backend communication.
 | Application server
 | Django + Django REST Framework
 | Device registration, policy checks, event ingestion and screenshot upload authorization.
 | Main database
 | PostgreSQL
 | Stores device, event, policy and screenshot metadata.
 | File storage
 | Private Amazon S3
 | Stores screenshot image files; images are not stored as database blobs.
 | Hosting
 | AWS EC2
 | Hosts the existing Django application architecture.
 | 

Figure 1: High-Level Windows Agent Architecture Context
5. HOW — How will the Windows Agent be built?
5.1 Proposed internal components
Agent component
 | Responsibility / implementation
 | Agent launcher
 | Starts the agent, loads configuration and starts required components.
 | Device registration & authentication
 | Registers the device with the backend and securely stores the issued device credential.
 | Application monitor
 | Reads the foreground application/window using Windows APIs; records app identity and timestamps.
 | Website monitor
 | Collects supported browser website activity through the browser-tracking mechanism approved for v1.
 | Activity monitor
 | Uses Windows idle/input activity signals to calculate active and idle intervals; does not collect key content.
 | Screenshot manager
 | Checks policy and capture schedule, captures an image, applies configured exclusions/blur behavior and prepares upload.
 | Sync manager
 | Sends event batches and upload status to DRF APIs; handles retry and duplicate prevention.
 | Local state store
 | Stores device configuration and, if approved, a bounded encrypted queue for offline events.
 | Logging & health
 | Records agent version, last successful sync, errors and operational status without logging sensitive content.
 | 

Figure 2: Internal Python Agent Component View
5.2 Recommended implementation technologies
Need
 | Technology / library
 | Use
 | Agent language
 | Python
 | Main Windows agent implementation; matches the architecture basis.
 | Windows integration
 | pywin32 / Windows APIs
 | Foreground-window details, Windows session integration and system events.
 | Process information
 | psutil
 | Process metadata and process checks where required.
 | Idle detection
 | Windows GetLastInputInfo API
 | Determine time since last user input; do not capture the input itself.
 | Screenshot capture
 | mss or Pillow
 | Capture screen image and encode it for upload.
 | API communication
 | requests or httpx
 | Call authenticated Django REST Framework endpoints over HTTPS.
 | Local queue (decision required)
 | SQLite
 | Optional bounded offline event queue; use only after confirming local buffering requirements.
 | Windows packaging
 | PyInstaller + approved installer technology
 | Package Python dependencies and create an employee-friendly installer.
 | 
Library choices above are implementation recommendations, not confirmed product decisions. The agent remains Python-based and Windows-native. The final library versions and installer technology should be selected during development.
6. WHEN — When does monitoring run?
At Windows sign-in or agent launch, the agent starts and checks its registration and configuration.
The agent retrieves the latest monitoring policy and applies the configured monitoring hours.
During allowed hours, it samples application/window and activity signals at configured intervals.
Screenshots are captured only at the configured frequency and only when policy, exclusions and monitoring hours permit them.
Events are sent periodically or in bounded batches; screenshot files use the separate upload flow.
When monitoring is disabled, outside allowed hours, or the device is excluded, collection/capture must stop as defined by policy.
7. Employee installation and first-time setup
The organization provides the signed Windows installer through its approved download or IT distribution channel.
The employee runs the installer and approves the required installation prompts.
The installer places the agent files and configures startup behavior according to the approved deployment method.
The employee signs in or enters the organization-provided registration code, depending on the final onboarding design.
The agent contacts the Django API over HTTPS and registers the device against the correct employee and organization.
The backend validates the employee/device relationship and returns the device configuration and monitoring policy.
The agent displays a successful registration/connection status and begins monitoring only when the policy allows it.
8. Monitoring data flow
Application, website and activity-signal flow:
Step
 | Action
 | 1
 | Windows Agent reads the active application/window and permitted activity signals.
 | 2
 | Agent creates timestamped events and associates them with its registered device.
 | 3
 | Agent sends events to a protected Django REST Framework endpoint over HTTPS.
 | 4
 | Django validates the device credential, employee, organization, payload and timestamps.
 | 5
 | Valid events are stored in PostgreSQL with server-resolved organization and employee ownership.
 | 6
 | Dashboard and reporting queries use stored records and enforce role/team/organization access.
 | 

Figure 3: Activity Event Collection and Batch Submission Flow
9. Screenshot capture and upload flow
The agent checks whether screenshots are enabled for the organization, employee, device and current monitoring time.
The agent checks applicable exclusions and the approved blur/sensitive-content rules.
If capture is permitted, the agent captures the screen image and prepares the file for upload.
The agent requests upload authorization from Django using its registered device identity.
Django re-checks policy, device ownership and authorization, creates screenshot metadata and returns a short-lived, object-specific S3 pre-signed URL.
The agent uploads the image directly to the private S3 bucket using that URL; the image bytes do not pass through the normal Django application server.
The agent reports upload completion. Django updates the screenshot record and upload status in PostgreSQL.
Authorized users view or download the screenshot through a permission-checked flow; each access is recorded in the audit log.

Figure 4: Policy-Controlled Screenshot Capture and S3 Direct Upload Flow
10. API responsibilities
API capability (logical)
 | Purpose
 | Device registration
 | Register a device and associate it with an employee and organization.
 | Agent configuration / policy
 | Return the monitoring schedule, enabled signals, screenshot settings and exclusions.
 | Activity ingestion
 | Accept validated application, website and activity-signal events.
 | Screenshot upload authorization
 | Check policy and issue a short-lived pre-signed S3 upload URL.
 | Screenshot upload completion
 | Confirm upload state and update screenshot metadata.
 | Agent heartbeat / status
 | Report agent version, last contact, connectivity and operational health.
 | 
11. Security, privacy and reliability
Use HTTPS/TLS for all agent-to-backend requests and protect device credentials at rest on the Windows device.
Use device-scoped authentication; validate device registration and resolve organization ownership on the server.
Never trust an organization_id supplied by the agent as proof of access.
Keep screenshot S3 storage private; use short-lived, object-specific upload/download links.
Apply organization monitoring hours, employee/device exclusions and screenshot policy before capture authorization.
Do not collect actual keystrokes, typed text, webcam or microphone data.
Record agent errors and sync health without writing tokens, screenshot contents or unnecessary personal data to logs.
Prevent duplicate ingestion using unique event identifiers or an equivalent idempotency mechanism.
The product requirements expect recovery from temporary network interruptions. A bounded local queue and retry mechanism should be designed and approved.
12. Testing, packaging and release
Test area
 | What must be verified
 | Installation
 | Clean install, upgrade, uninstall, startup behavior and supported Windows versions.
 | Monitoring
 | Correct foreground application, idle/active calculations, timestamps and monitoring-hour enforcement.
 | Website tracking
 | Supported browser coverage, URL handling, exclusions and no duplicate events.
 | Screenshots
 | Policy checks, capture schedule, exclusions/blur, upload success/failure and access audit.
 | API/security
 | Invalid device rejection, token expiry, tenant isolation, payload validation and unauthorized screenshot access.
 | Network recovery
 | Offline behavior, retry, duplicate prevention and recovery after connection returns.
 | Performance
 | CPU/memory usage, event volume, screenshot size and impact on employee workstation.
 | Release
 | Versioned installer, code signing, staged rollout, update/rollback and agent health monitoring.
 | 
13. Decisions to confirm before implementation
Open decision
 | Why it matters
 | Supported Windows versions and 32/64-bit scope
 | Determines compatibility and test coverage.
 | Installer and auto-start approach; admin privileges
 | Determines employee installation experience and support requirements.
 | Browser support and extension responsibility
 | Determines how website activity is captured and deduplicated.
 | Monitoring intervals and idle threshold
 | Determines event volume and active/idle calculations.
 | Screenshot frequency, blur, exclusions, retention and access rights
 | Determines privacy behavior, storage cost and authorization.
 | Offline buffering approval, encryption and local retention
 | Required to meet network-interruption/data-recovery expectations safely.
 | Device registration and multiple-device policy
 | Determines employee onboarding and device lifecycle.
 | Agent update mechanism and code-signing certificate
 | Required for secure distribution and maintenance.
 | RDP / Citrix / VDI support
 | Determines session detection and screenshot behavior in virtual environments.
 | 
14. Architecture summary
The initial release uses a Python native Windows Agent installed on employee Windows devices. It collects permitted application/window usage, supported website activity and active/idle signals, and captures screenshots only under the approved monitoring policy. The agent communicates with the existing Django REST Framework backend over HTTPS. PostgreSQL stores structured events and screenshot metadata, while private Amazon S3 stores screenshot images. Device authentication, organization isolation, role permissions, privacy rules and audit logging are enforced by the backend.
Document note: This TAD is a focused agent-level design. Where the supplied requirements did not define exact implementation values, the item is marked as proposed or open for confirmation rather than treated as an approved decision.
