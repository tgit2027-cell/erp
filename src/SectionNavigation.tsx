import { useEffect, useId, useState, type KeyboardEvent, type ReactNode } from "react";
import "./section-navigation.css";
import { destinationKey } from "./workspace-hash";

export type NavigationSection = {
  id: string;
  name: string;
  icon: string;
  children: { id: string; label: string; selector?: string; favoriteable?: boolean }[];
};

type SectionNavigationProps = {
  sections: NavigationSection[];
  activeSection: string;
  activeChild: string | null;
  navigationRevision: number;
  onNavigate: (section: string, child: string) => void;
  renderFavorite: (id: string, label?: string) => ReactNode;
};

export default function SectionNavigation({
  sections,
  activeSection,
  activeChild,
  navigationRevision,
  onNavigate,
  renderFavorite,
}: SectionNavigationProps) {
  const instanceId = useId();
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    () => new Set(sections.some((section) => section.id === activeSection) ? [activeSection] : []),
  );

  useEffect(() => {
    if (!sections.some((section) => section.id === activeSection)) {
      setExpandedSections((current) => current.size ? new Set() : current);
      return;
    }
    setExpandedSections((current) => {
      if (current.size === 1 && current.has(activeSection)) return current;
      return new Set([activeSection]);
    });
  }, [navigationRevision, activeSection, activeChild]);

  const toggleSection = (sectionId: string) => {
    setExpandedSections((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) next.delete(sectionId);
      else return new Set([sectionId]);
      return next;
    });
  };

  const handleSectionKeyDown = (event: KeyboardEvent<HTMLButtonElement>, sectionId: string) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setExpandedSections(new Set([sectionId]));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      setExpandedSections((current) => {
        if (!current.has(sectionId)) return current;
        const next = new Set(current);
        next.delete(sectionId);
        return next;
      });
    }
  };

  return (
    <div className="section-navigation">
      {sections.map((section) => {
        const expanded = expandedSections.has(section.id);
        const childrenId = `${instanceId}-section-${encodeURIComponent(section.id)}`;
        return (
          <div className={`section-navigation-group${expanded ? " is-expanded" : ""}`} key={section.id}>
            <div className="section-navigation-heading">
              <button
                type="button"
                className={`nav-section-toggle${section.id === activeSection ? " is-current" : ""}`}
                aria-expanded={expanded}
                aria-controls={childrenId}
                aria-label={`${section.name}: ${expanded ? "collapse" : "expand"} section`}
                title={section.name}
                onClick={() => toggleSection(section.id)}
                onKeyDown={(event) => handleSectionKeyDown(event, section.id)}
              >
                <span className="nav-icon nav-number" aria-hidden="true">{section.icon}</span>
                <span className="nav-section-name">{section.name}</span>
                <span className="section-navigation-chevron" aria-hidden="true" />
              </button>
              <span className="section-navigation-favorite">{renderFavorite(section.id)}</span>
            </div>
              <ul className="section-navigation-children" id={childrenId} hidden={!expanded}>
                {section.children.map((child, index) => {
                  const isActive = section.id === activeSection &&
                    (activeChild === child.id || (activeChild === null && index === 0));
                  return (
                    <li key={child.id}>
                      <div className="nav-row nav-child-row">
                        <button
                          type="button"
                          className={`nav-item${isActive ? " active" : ""}`}
                          onClick={() => onNavigate(section.id, child.id)}
                          aria-current={isActive ? "page" : undefined}
                          data-workspace-link={`${section.id}/${child.id}`}
                          aria-label={child.label}
                          title={child.label}
                        >
                          <span>{child.label}</span>
                        </button>
                        {child.id === "main" || child.favoriteable === false ? null : renderFavorite(destinationKey(section.id, child.id), child.label)}
                      </div>
                    </li>
                  );
                })}
              </ul>
          </div>
        );
      })}
    </div>
  );
}
