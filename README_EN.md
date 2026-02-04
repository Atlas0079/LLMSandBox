# LLM-Driven Agent Society Simulation Engine
## LLM-Based Agent Society Simulation Evolution System

[中文版本](./README.md)

### 1. Abstract

The ultimate goal of this project is to implement a multi-agent simulation system to observe the decision-making and interaction processes of agents driven by Large Language Models (LLM) in a dynamic environment.
The system provides an open virtual environment where agents perceive local information, propose action requests, and the world state is updated after execution by system rules.
Compared to fully script-dependent methods, this project focuses more on the performance and limitations of "autonomous behavior under rule constraints" in long-running operations.

---

### 2. Core Concept Definition

#### 2.1 Discrete Time Simulation

System time is divided into fixed time steps (Ticks).
In each time step $t$, the system schedules all agents **sequentially**. Each agent makes perception and decisions based on the **immediate world state** (including changes caused by agents scheduled earlier in the same time step), and the generated actions are executed immediately to update the world state. This means that agents scheduled later can see the results of actions just completed by agents scheduled earlier.

#### 2.2 Open Environment

The system does not set fixed "plot goals" or "preset task flows".
The environment consists of discrete **Entities** (such as resources, tools, buildings).
The environment also consists of discrete **Locations**: Locations are basic spatial nodes used to carry rules about "who is in the same place as whom, and therefore can see/interact".
The system only defines atomic **physical interaction logic** (such as "eating" increases energy, "chopping" consumes stamina and produces wood), without restricting how agents use these rules.
On this basis, the system allows agents to continuously interact under resource and constraint conditions, observing whether more complex collaboration or division of labor phenomena will emerge.

#### 2.3 Symbol Grounding

This is the core technical difficulty of this system. The output of LLM is abstract natural language (such as "I want to eat that red apple"), while the computer system can only execute precise instructions (such as `Consume(Target_ID=1024)`). This system designs a **Two-Layer Cognitive Architecture** to bridge this "language-behavior" gap (see Section 4).

---

### 3. System Architecture

The system adopts a **Data-Driven** and **Modular State** design.

#### 3.0 Motivation for Data-Driven

This project externalizes "entity templates" and "interaction recipes" as data (e.g., JSON) as much as possible, mainly considering two points:
- The data structure is clear, making it easy for LLM to check the consistency of existing templates and recipes under given constraints.
- The long-term goal is to introduce a "God Agent" (World Editor), who does not directly play a role in the world but adjusts "world axioms" (e.g., adding/deleting recipes, modifying template parameters, adjusting resource generation rates) with restricted permissions. Such modifications need to pass structural validation and rule verification before taking effect to prevent invalid configurations from breaking the simulation.

#### 3.1 Simulation Kernel

The simulation kernel can be understood as the system's "scheduler + write entry", responsible for advancing the world in a controllable and reproducible manner. Its goal is not to "calculate all behaviors very realistically", but to clearly define the **boundaries of state updates**, making debugging and extension simple.
- **Single Source of Truth**: All world data is centralized in `WorldState` (entities, locations, containers, tasks, time, and logs). Other modules only read it or submit change requests through agreed interfaces.
- **Time Advancement**: Each Tick first advances time, then drives the `per_tick` logic of each entity/component, allowing "decay", "task progress", "cognitive decision" etc. to happen on a unified beat.
- **Serialized Execution**: Entities are processed sequentially within the same Tick to avoid concurrency conflicts; and ensure subsequent decisions see the latest world.
- **Unique Write Entry**: Components do not directly modify details of `WorldState`, but construct atomic `Effect`s and execute them immediately via the executor service **synchronously**; this ensures that entities scheduled later can immediately see changes in the world state, while keeping the state change path consistent, recordable, and replayable.
- **Events and Observations**: Each Tick and execution of every Effect generates event records, which are used for debugging and providing readable narrative input of "what happened recently" for the perception system.

#### 3.2 Entity-Component Model

- **Entity**: The essence of an entity is merely a **Unique Identifier (ID)**. It contains no logic or data itself, just like an empty container.
- **Component**: A component is an **atomic encapsulation of functional features and data**. It is the smallest unit that gives an entity specific "meaning", essentially a "capability contract".
    - **As Data Container**: Components store specific state data.
        - *Example*: `CreatureComponent` maintains physiological homeostasis indicators of an organism (such as nutrition, hydration), defining the existence of the entity at the biological level.
    - **As Behavioral Enabler**: Mounting a component activates specific system logic.
        - *Example*: Only with `AgentControlComponent` mounted will the **Cognitive System** intervene, allocating LLM computing resources to the entity and taking over its decision-making power.
        - *Example*: Only with `ContainerComponent` mounted does the **Physical System** allow the entity to spatially "contain" other objects (such as backpacks or boxes).
    - This decoupled design allows developers to freely define experimental subjects by "stacking blocks" without modifying underlying code to verify different hypotheses.

##### 3.2.1 Location and Spatial Model

- **Location**: Locations are basic nodes of discrete space, maintaining an index of "what entities are currently at this location", and can form a location graph via connection relationships (e.g., Location A can go to Location B).
- **Entity ↔ Location**: A location usually contains multiple entities; an entity should usually only be in one location at the same time (or inside a container, which is at a location).
- **Location and Perception/Interaction**: The perception system usually collects visible entities bounded by "current Location"; the interaction system also usually requires the target entity and the actor to be in the same Location (or satisfy container visibility) to match a recipe.
- **Container is a Supplement to Location**: Location solves spatial relationships "in the same place"; Container solves hierarchical relationships "in the same place but contained/occluded" (e.g., items inside a backpack may be invisible).

#### 3.3 Component Library Overview

##### Implemented
- **Basic Attributes**
    - `TagComponent`: **Semantic Tag System**. Tags entities with "Edible", "Flammable", etc., for interaction rules (`Recipes`) matching.
    - `AgentComponent`: **Identity Definition**. Stores agent's name, personality description (Persona), and basic common sense.
- **Physiology and Survival**
    - `CreatureComponent`: **Biological Signs**. Maintains homeostasis indicators like Health Points (HP), Energy, Nutrition, simulating natural decay caused by entropy increase.
- **Abilities and Behavior**
    - `WorkerComponent`: **Labor Engine**. Empowers entities to execute long-cycle Tasks (e.g., "Chopping tree (30%)") and manages task progress.
    - `ContainerComponent`: **Spatial Container**. Allows entities to store other objects internally (such as backpacks, boxes), supporting infinite levels of recursive nesting.
- **Decision and Control**
    - `AgentControlComponent`: **Cognitive Interface**. Bridge connecting LLM, responsible for uploading perception data to the model and parsing natural language intents generated by the model into instructions.
    - `DecisionArbiterComponent`: **Decision Arbiter**. Judges whether to interrupt current behavior based on rules (such as "hungry" or "task completed"), triggering a new OODA loop.
    - `TaskHostComponent`: **Plan Management**. Maintains the agent's To-Do Task Queue.

##### Planned
- `MemoryComponent`: **Long-term Memory Interface**. Connects to vector database for storage and retrieval of episodic memory.
- `SocialComponent`: **Social Relationship Representation**. Maintains states like intimacy/trust, used to drive more complex interaction strategies.
- `SkillComponent`: **Skills and Proficiency**. Associates efficiency/success rate with experience accumulation, supporting more stable long-term behavioral differences.
- `WorldEditorAgent`: **Meta-Level Controller**. Reviews and modifies entity templates and recipes ("world axioms") with restricted permissions, used for rapid iteration and experimental comparison.

---

### 4. Agent Cognitive Architecture

Agents are cognitive entities with **OODA Loop (Observe-Orient-Decide-Act)**. They have two layers, which can call different LLM models respectively.

#### 4.1 Cognitive Layering
- **Layer 1: Planner**
    - Input: Agent's self-description (name/personality/common sense summary), current time step info, current location and visible entity list, recent interaction records, current trigger reason (e.g., "low nutrition/task completed").
    - Output: Intent and brief plan in natural language form (excluding executable low-level instructions).
    - Function: Forms high-level decisions on "what to do in the next stage" under given constraints and limited perception.
    - *Example*: "My energy is low now, I'll look for edible items to replenish nutrition first."
- **Layer 2: Grounder**
    - Input: Intent text output by Planner, available verb set (determined jointly by existing Recipes and visible targets), visible entity list and their IDs, necessary context (e.g., reason for failure in previous round).
    - Output: Structured action request list (Action), each containing `verb`, `target_id`, and optional `parameters`.
    - Function: Maps natural language intent to "system-permitted and grammatically correct" action requests; if the action does not comply with rules, it will be rejected by the recipe engine later and the reason for failure will be recorded.
    - *Example*: Generates `{"verb": "PickUp", "target_id": "Apple_01", "parameters": {}}`.

#### 4.2 Interaction
The system's physical rules are defined in external data files (`Recipes.json`), constituting the world's **Axiom System**. In implementation, LLM cannot "directly change the world"; it must first propose a structured request, which is translated and executed by the system according to rules. To this end, the engine splits interaction into four concepts and forms a fixed data flow:

1.  **Action**: "Interaction Application" proposed by the agent, essentially a **filled form**.
    - *Definition*: `verb` (action name) + `target_id` (target entity) + `parameters` (optional parameters). Action itself does not modify the world, only describes "what I want to do, to whom".
    - *Layman's understanding*: *Like "I want to chop this tree". Haven't started yet.*
2.  **Recipe**: Whitelist of allowed behaviors in the world, a **lookup table rule from Action to effect sequence**.
    - *Matching*: The engine matches recipes based on `verb` + target's `Tags` + parameter constraints; rejects if no match (e.g., `NO_TARGET` / `NO_RECIPE`).
    - *Layman's understanding*: *System checks table: What conditions are needed to chop a tree? Target must have Wood tag; and this is not instant, takes some time.*
3.  **Task (Continuous Task)**: When a recipe declares it takes time, the Action will not directly produce a final result, but will be translated into a progressable process state.
    - *Mechanism*: System first generates `CreateTask` effect to create a task; then `WorkerComponent` advances progress in each Tick, producing `tick_effects` if necessary; triggers `FinishTask` upon completion and executes completion effects solidified in the task.
    - *Layman's understanding*: *After Action matches successfully, the agent is occupied until the task is completed or interrupted.*
4.  **Effect (Atomic State Change)**: The **unique write entry** to `WorldState`, executed one by one by the executor.
    - *Definition*: Effect is the smallest operator for world change. Recipe outputs are split into a series of effects, executed in order.
    - *Layman's understanding*: *When the task is completed, the system truly modifies the world: tree is destroyed, wood is produced, stamina changes, etc.*

> **Chain Flow**: Agent initiates `Action` $\rightarrow$ Matches `Recipe` $\rightarrow$ Instantiates `Task` $\rightarrow$ Produces `Effect` over time $\rightarrow$ Modifies `WorldState`.

---

### 5. Development Roadmap

This project is currently in the prototype stage, and the core framework can run end-to-end. The following lists the completed parts and future plans by stage.

#### Completed Parts
    - Implemented discrete time step driven world state evolution (`WorldState` + `WorldManager.step()`), supporting Tick-level event recording for debugging and replay.
    - Implemented Entity-Component based modeling, supporting construction of different types of entities (agents, items, containers, etc.) via templates and component combinations.
    - Supported combined index of Location and Container: Entities can exist directly in a location, or be stored in multi-level nested containers, with visibility controlled by the perception system.
    - Implemented complete link from Action → Recipe → Task → Effect, including immediate effects and continuous tasks advancing over time (such as eating, resting behaviors).
    - Implemented Planner + Grounder dual-layer architecture: Planner responsible for high-level intent generation, Grounder responsible for translating intent into constrained action requests; actions are then grounded to world state by recipe engine and executor.
    - Implemented rule-based DecisionArbiter, capable of interrupting current tasks under conditions like hunger or task completion, triggering a new round of decision-making.

#### Future Plans
- First, need to introduce long-term memory and episodic memory components, currently researching LightRAG.
- Currently Path is not actually completed, there are no connections between locations.
- Need to implement additional components, depending on what kind of environment we want to simulate. For example, to simulate farmers working in farms, crop-related components, entity templates, and recipes might be needed.
- Add explicit communication actions between multi-agents, observing the emergence of simple social structures.
- Join "WorldEditorAgent" to review and adjust entity templates and recipes ("world axioms"), supporting comparative experiments of different "world settings".
- Use "WorldEditorAgent" to expand the number of entity templates and recipes to support more complex world simulations.
---
