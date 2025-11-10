# utils/recommendations.py - Recommendation algorithms
from .database import get_db
import re

def get_recommendations(user_id):
    """Get personalized internship recommendations for a user."""
    conn = get_db()
    irs = conn.cursor()
    
    # Content-based recommendations
    content_recs = content_based_recommendations(user_id, irs)
    
    # Collaborative filtering recommendations
    collab_recs = collaborative_filtering(user_id, irs)
    
    # Combine and deduplicate recommendations
    all_recs = {rec['id']: rec for rec in content_recs}
    
    # Add collaborative recommendations (keep higher similarity if duplicate)
    for rec in collab_recs:
        if rec['id'] not in all_recs:
            all_recs[rec['id']] = rec
        else:
            # If collaborative recommendation has higher similarity, use it
            if rec['similarity'] > all_recs[rec['id']]['similarity']:
                all_recs[rec['id']] = rec
    
    return list(all_recs.values())

def content_based_recommendations(user_id, irs):
    """Content-based recommendation algorithm using skill matching."""
    def normalize_skills(raw_value):
        """Normalize a raw skills string into a clean set of canonical skills."""
        if not raw_value:
            return set()
        # Split on commas, semicolons, slashes, pipes, or any whitespace
        # This handles inputs like "Python, React, Java" and "Python React Java"
        tokens = re.split(r"[,\;/\|\s]+", str(raw_value))
        normalized = set()
        alias_map = {
            'js': 'javascript',
            'node.js': 'node',
            'nodejs': 'node',
            'reactjs': 'react',
            'react.js': 'react',
            'py': 'python',
            'sql db': 'sql',
            'postgre': 'postgresql',
            'postgres': 'postgresql',
            'ui/ux': 'uiux',
            'c sharp': 'c#',
            'c-sharp': 'c#',
            'cpp': 'c++',
        }
        for t in tokens:
            skill = t.strip().lower()
            if not skill:
                continue
            # Remove trailing periods and extra spaces
            skill = skill.strip(". ").replace("&", "and")
            # Canonicalize some common variants
            skill = alias_map.get(skill, skill)
            normalized.add(skill)
        return normalized

    # Get student's skills
    irs.execute("SELECT skills FROM profiles WHERE user_id=?", (user_id,))
    profile = irs.fetchone()
    
    if not profile or not profile['skills']:
        return []
    
    student_skills = normalize_skills(profile['skills'])
    
    # Get all internships
    irs.execute("SELECT * FROM internships")
    internships = irs.fetchall()
    
    # Calculate similarity for each internship
    recommendations = []
    for internship in internships:
        required_skills = normalize_skills(internship['required_skills']) if internship['required_skills'] else set()
        
        if not required_skills:
            continue
            
        # Blended similarity emphasizing required skill coverage
        intersection_count = len(student_skills & required_skills)
        union_count = len(student_skills | required_skills)
        jaccard = (intersection_count / union_count) if union_count > 0 else 0.0
        coverage_required = (intersection_count / len(required_skills)) if len(required_skills) > 0 else 0.0
        coverage_student = (intersection_count / len(student_skills)) if len(student_skills) > 0 else 0.0

        # Weighted blend to improve perceived accuracy:
        # - required coverage: primary (meets job requirements)
        # - jaccard: secondary (overall overlap considering list sizes)
        # - student coverage: slight bonus (how much of student's skills are utilized)
        similarity = 0.6 * coverage_required + 0.3 * jaccard + 0.1 * coverage_student

        # If all required skills are satisfied, force a perfect match
        if coverage_required == 1.0 and len(required_skills) > 0:
            similarity = 1.0
        
        if similarity >= 0.25:  # Slightly higher threshold after more precise scoring
            # Get company information
            irs.execute("SELECT name FROM users WHERE id=?", (internship['company_id'],))
            company = irs.fetchone()
            company_name = company['name'] if company else 'Unknown Company'
            
            recommendations.append({
                'id': internship['id'],
                'title': internship['title'],
                'description': internship['description'],
                'required_skills': internship['required_skills'],
                'posted_at': internship['posted_at'],
                'company_name': company_name,
                'company_id': internship['company_id'],
                'similarity': round(similarity, 4),
                'type': 'Content-based'
            })
    
    # Sort by similarity
    recommendations.sort(key=lambda x: x['similarity'], reverse=True)
    return recommendations[:5]

def collaborative_filtering(user_id, irs):
    """Collaborative filtering recommendation algorithm."""
    # Get applications of similar students
    # Step 1: Find students with similar applications
    irs.execute("SELECT student_id, internship_id FROM applications")
    all_applications = irs.fetchall()
    
    # Build user-item matrix
    user_items = {}
    for app in all_applications:
        student_id = app['student_id']
        internship_id = app['internship_id']
        if student_id not in user_items:
            user_items[student_id] = set()
        user_items[student_id].add(internship_id)
    
    # Find similar students based on Jaccard similarity
    current_user_apps = user_items.get(user_id, set())
    similar_students = []
    
    for student_id, apps in user_items.items():
        if student_id == user_id:
            continue
            
        intersection = len(current_user_apps & apps)
        union = len(current_user_apps | apps)
        similarity = intersection / union if union > 0 else 0
        
        if similarity > 0:
            similar_students.append((student_id, similarity))
    
    # Sort by similarity
    similar_students.sort(key=lambda x: x[1], reverse=True)
    
    # Get top internships from similar students
    recommendations = []
    seen_internships = set(current_user_apps)  # Exclude internships already applied to
    
    for student_id, similarity in similar_students[:3]:  # Top 3 similar students
        for internship_id in user_items[student_id]:
            if internship_id not in seen_internships:
                irs.execute("SELECT * FROM internships WHERE id=?", (internship_id,))
                internship = irs.fetchone()
                if internship:
                    # Get company information
                    irs.execute("SELECT name FROM users WHERE id=?", (internship['company_id'],))
                    company = irs.fetchone()
                    company_name = company['name'] if company else 'Unknown Company'
                    
                    recommendations.append({
                        'id': internship['id'],
                        'title': internship['title'],
                        'description': internship['description'],
                        'required_skills': internship['required_skills'],
                        'posted_at': internship['posted_at'],
                        'company_name': company_name,
                        'company_id': internship['company_id'],
                        'similarity': similarity,
                        'type': 'Collaborative'
                    })
                    seen_internships.add(internship_id)
    
    return recommendations[:5]  # Top 5

def hybrid_recommendations(user_id, content_weight=0.6, collab_weight=0.4):
    """Hybrid recommendation combining content-based and collaborative filtering."""
    conn = get_db()
    irs = conn.cursor()
    
    # Get recommendations from both methods
    content_recs = content_based_recommendations(user_id, irs)
    collab_recs = collaborative_filtering(user_id, irs)
    
    # Create a dictionary to store combined scores
    hybrid_scores = {}
    
    # Process content-based recommendations
    for rec in content_recs:
        internship_id = rec['id']
        hybrid_scores[internship_id] = {
            'data': rec,
            'content_score': rec['similarity'],
            'collab_score': 0,
            # If only content exists, use the raw content score (not weighted down)
            'combined_score': rec['similarity'],
            'sources': ['content']
        }
    
    # Process collaborative recommendations
    for rec in collab_recs:
        internship_id = rec['id']
        if internship_id in hybrid_scores:
            # Update existing with collaborative score
            hybrid_scores[internship_id]['collab_score'] = rec['similarity']
            # If we have both signals, apply weighted combination
            hybrid_scores[internship_id]['combined_score'] = (
                hybrid_scores[internship_id]['content_score'] * content_weight +
                rec['similarity'] * collab_weight
            )
            hybrid_scores[internship_id]['sources'].append('collaborative')
        else:
            # Add new collaborative recommendation
            hybrid_scores[internship_id] = {
                'data': rec,
                'content_score': 0,
                'collab_score': rec['similarity'],
                # If only collaborative exists, use the raw collaborative score (not weighted down)
                'combined_score': rec['similarity'],
                'sources': ['collaborative']
            }
    
    # Convert to final list format
    recommendations = []
    for internship_id, scores in hybrid_scores.items():
        rec_data = scores['data'].copy()
        rec_data['similarity'] = scores['combined_score']
        rec_data['content_similarity'] = scores['content_score']
        rec_data['collab_similarity'] = scores['collab_score']
        
        # Show which algorithm was used - including Hybrid as an option
        if 'content' in scores['sources'] and 'collaborative' in scores['sources']:
            rec_data['type'] = 'Hybrid'
            rec_data['algorithm'] = 'Hybrid'
        elif 'content' in scores['sources']:
            rec_data['type'] = 'Content-Based'
            rec_data['algorithm'] = 'Content-Based'
        else:
            rec_data['type'] = 'Collaborative'
            rec_data['algorithm'] = 'Collaborative'
        
        recommendations.append(rec_data)
    
    # Sort by combined similarity score
    def algo_priority(rec):
        # Hybrid first (0), then Content-Based (1), then Collaborative (2)
        rec_type = rec.get('type') or rec.get('algorithm') or ''
        if rec_type.lower() == 'hybrid':
            return 0
        if rec_type.lower() in ('content-based', 'content'):
            return 1
        return 2  # collaborative or unknown

    recommendations.sort(key=lambda x: (algo_priority(x), -x['similarity']))
    return recommendations[:10]  # Return top 10 hybrid recommendations


def get_recommendations(user_id, method='hybrid'):
    """Get personalized internship recommendations for a user.
    
    Args:
        user_id: The user ID to get recommendations for
        method: 'hybrid', 'content', or 'collaborative'
    
    Examples:
        # Get content-based recommendations only
        content_recs = get_recommendations(user_id, 'content')
        
        # Get collaborative filtering recommendations only  
        collab_recs = get_recommendations(user_id, 'collaborative')
        
        # Get hybrid recommendations (default) - shows which algorithm was used
        hybrid_recs = get_recommendations(user_id, 'hybrid')
    """
    conn = get_db()
    irs = conn.cursor()
    
    if method == 'content':
        recs = content_based_recommendations(user_id, irs)
        # Add algorithm info for content-only
        for rec in recs:
            rec['algorithm'] = 'Content-Based'
        return recs
    elif method == 'collaborative':
        recs = collaborative_filtering(user_id, irs)
        # Add algorithm info for collaborative-only
        for rec in recs:
            rec['algorithm'] = 'Collaborative'
        return recs
    else:  # hybrid
        return hybrid_recommendations(user_id)