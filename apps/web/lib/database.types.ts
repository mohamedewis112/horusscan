export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      analysis_layers: {
        Row: {
          analysis_run_id: string
          created_at: string
          data: Json
          id: string
          layer_type: string
          provenance: Json
          raster_uri: string | null
          stats: Json
          vector_uri: string | null
        }
        Insert: {
          analysis_run_id: string
          created_at?: string
          data?: Json
          id?: string
          layer_type: string
          provenance?: Json
          raster_uri?: string | null
          stats?: Json
          vector_uri?: string | null
        }
        Update: {
          analysis_run_id?: string
          created_at?: string
          data?: Json
          id?: string
          layer_type?: string
          provenance?: Json
          raster_uri?: string | null
          stats?: Json
          vector_uri?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "analysis_layers_analysis_run_id_fkey"
            columns: ["analysis_run_id"]
            isOneToOne: false
            referencedRelation: "analysis_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      analysis_run_scenes: {
        Row: {
          analysis_run_id: string
          created_at: string
          id: string
          ordinal: number
          satellite_scene_id: string
          sensor_role: string
        }
        Insert: {
          analysis_run_id: string
          created_at?: string
          id?: string
          ordinal?: number
          satellite_scene_id: string
          sensor_role: string
        }
        Update: {
          analysis_run_id?: string
          created_at?: string
          id?: string
          ordinal?: number
          satellite_scene_id?: string
          sensor_role?: string
        }
        Relationships: [
          {
            foreignKeyName: "analysis_run_scenes_analysis_run_id_fkey"
            columns: ["analysis_run_id"]
            isOneToOne: false
            referencedRelation: "analysis_runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "analysis_run_scenes_satellite_scene_id_fkey"
            columns: ["satellite_scene_id"]
            isOneToOne: false
            referencedRelation: "satellite_scenes"
            referencedColumns: ["id"]
          },
        ]
      }
      analysis_runs: {
        Row: {
          aoi_id: string
          created_at: string
          data_quality_score: number | null
          error_message: string | null
          finished_at: string | null
          git_commit: string | null
          id: string
          parameters: Json
          progress_stage: string | null
          project_id: string
          requested_by: string
          started_at: string | null
          status: string
        }
        Insert: {
          aoi_id: string
          created_at?: string
          data_quality_score?: number | null
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          parameters?: Json
          progress_stage?: string | null
          project_id: string
          requested_by: string
          started_at?: string | null
          status?: string
        }
        Update: {
          aoi_id?: string
          created_at?: string
          data_quality_score?: number | null
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          parameters?: Json
          progress_stage?: string | null
          project_id?: string
          requested_by?: string
          started_at?: string | null
          status?: string
        }
        Relationships: [
          {
            foreignKeyName: "analysis_runs_aoi_id_fkey"
            columns: ["aoi_id"]
            isOneToOne: false
            referencedRelation: "areas_of_interest"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "analysis_runs_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      anomalies: {
        Row: {
          analysis_run_id: string
          confidence: string
          created_at: string
          evidence: Json
          evidence_agreement: string
          explanation: Json
          geom: unknown
          horus_evidence_score: number | null
          id: string
          priority: string
          status: string
          uncertainty: string
        }
        Insert: {
          analysis_run_id: string
          confidence?: string
          created_at?: string
          evidence?: Json
          evidence_agreement?: string
          explanation?: Json
          geom: unknown
          horus_evidence_score?: number | null
          id?: string
          priority?: string
          status?: string
          uncertainty?: string
        }
        Update: {
          analysis_run_id?: string
          confidence?: string
          created_at?: string
          evidence?: Json
          evidence_agreement?: string
          explanation?: Json
          geom?: unknown
          horus_evidence_score?: number | null
          id?: string
          priority?: string
          status?: string
          uncertainty?: string
        }
        Relationships: [
          {
            foreignKeyName: "anomalies_analysis_run_id_fkey"
            columns: ["analysis_run_id"]
            isOneToOne: false
            referencedRelation: "analysis_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      areas_of_interest: {
        Row: {
          area_km2: number
          created_at: string
          created_by: string
          geom: unknown
          id: string
          name: string
          project_id: string
        }
        Insert: {
          area_km2: number
          created_at?: string
          created_by: string
          geom: unknown
          id?: string
          name: string
          project_id: string
        }
        Update: {
          area_km2?: number
          created_at?: string
          created_by?: string
          geom?: unknown
          id?: string
          name?: string
          project_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "areas_of_interest_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      audit_logs: {
        Row: {
          action: string
          actor_id: string | null
          created_at: string
          details: Json
          entity_id: string | null
          entity_type: string | null
          id: number
          project_id: string | null
        }
        Insert: {
          action: string
          actor_id?: string | null
          created_at?: string
          details?: Json
          entity_id?: string | null
          entity_type?: string | null
          id?: never
          project_id?: string | null
        }
        Update: {
          action?: string
          actor_id?: string | null
          created_at?: string
          details?: Json
          entity_id?: string | null
          entity_type?: string | null
          id?: never
          project_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "audit_logs_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      foundation_features: {
        Row: {
          analysis_run_id: string | null
          created_at: string
          data: Json
          feature_type: string
          id: string
          model_key: string
          provenance: Json
          summary: Json
          validation_run_id: string | null
        }
        Insert: {
          analysis_run_id?: string | null
          created_at?: string
          data?: Json
          feature_type: string
          id?: string
          model_key: string
          provenance?: Json
          summary?: Json
          validation_run_id?: string | null
        }
        Update: {
          analysis_run_id?: string | null
          created_at?: string
          data?: Json
          feature_type?: string
          id?: string
          model_key?: string
          provenance?: Json
          summary?: Json
          validation_run_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "foundation_features_analysis_run_id_fkey"
            columns: ["analysis_run_id"]
            isOneToOne: false
            referencedRelation: "analysis_runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "foundation_features_validation_run_id_fkey"
            columns: ["validation_run_id"]
            isOneToOne: false
            referencedRelation: "validation_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      model_tournament_entries: {
        Row: {
          availability_reason: string | null
          created_at: string
          duration_ms: number | null
          id: string
          metrics: Json
          model_key: string
          model_metadata: Json
          status: string
          tournament_run_id: string
        }
        Insert: {
          availability_reason?: string | null
          created_at?: string
          duration_ms?: number | null
          id?: string
          metrics?: Json
          model_key: string
          model_metadata?: Json
          status: string
          tournament_run_id: string
        }
        Update: {
          availability_reason?: string | null
          created_at?: string
          duration_ms?: number | null
          id?: string
          metrics?: Json
          model_key?: string
          model_metadata?: Json
          status?: string
          tournament_run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "model_tournament_entries_tournament_run_id_fkey"
            columns: ["tournament_run_id"]
            isOneToOne: false
            referencedRelation: "model_tournament_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      model_tournament_runs: {
        Row: {
          competitors: string[]
          created_at: string
          dataset_version: string
          error_message: string | null
          finished_at: string | null
          git_commit: string | null
          id: string
          parameters: Json
          ranking: Json
          requested_by: string
          split: string
          started_at: string | null
          status: string
          winner: string | null
        }
        Insert: {
          competitors?: string[]
          created_at?: string
          dataset_version: string
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          parameters?: Json
          ranking?: Json
          requested_by: string
          split: string
          started_at?: string | null
          status?: string
          winner?: string | null
        }
        Update: {
          competitors?: string[]
          created_at?: string
          dataset_version?: string
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          parameters?: Json
          ranking?: Json
          requested_by?: string
          split?: string
          started_at?: string | null
          status?: string
          winner?: string | null
        }
        Relationships: []
      }
      model_versions: {
        Row: {
          calibration_version: string | null
          checkpoint: string | null
          code_commit: string | null
          created_at: string
          dataset_version: string | null
          id: string
          metadata: Json
          model_name: string
          model_version: string
          preprocessing_version: string | null
        }
        Insert: {
          calibration_version?: string | null
          checkpoint?: string | null
          code_commit?: string | null
          created_at?: string
          dataset_version?: string | null
          id?: string
          metadata?: Json
          model_name: string
          model_version: string
          preprocessing_version?: string | null
        }
        Update: {
          calibration_version?: string | null
          checkpoint?: string | null
          code_commit?: string | null
          created_at?: string
          dataset_version?: string | null
          id?: string
          metadata?: Json
          model_name?: string
          model_version?: string
          preprocessing_version?: string | null
        }
        Relationships: []
      }
      profiles: {
        Row: {
          created_at: string
          display_name: string | null
          id: string
          role: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          display_name?: string | null
          id: string
          role?: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          display_name?: string | null
          id?: string
          role?: string
          updated_at?: string
        }
        Relationships: []
      }
      projects: {
        Row: {
          created_at: string
          description: string | null
          id: string
          name: string
          owner_id: string
          status: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          description?: string | null
          id?: string
          name: string
          owner_id: string
          status?: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          description?: string | null
          id?: string
          name?: string
          owner_id?: string
          status?: string
          updated_at?: string
        }
        Relationships: []
      }
      satellite_scenes: {
        Row: {
          acquisition_time: string | null
          assets: Json
          cloud_cover: number | null
          collection: string
          created_at: string
          data_quality_score: number | null
          footprint: unknown
          id: string
          item_id: string
          metadata: Json
          provider: string
          sensor: string
        }
        Insert: {
          acquisition_time?: string | null
          assets?: Json
          cloud_cover?: number | null
          collection: string
          created_at?: string
          data_quality_score?: number | null
          footprint?: unknown
          id?: string
          item_id: string
          metadata?: Json
          provider: string
          sensor: string
        }
        Update: {
          acquisition_time?: string | null
          assets?: Json
          cloud_cover?: number | null
          collection?: string
          created_at?: string
          data_quality_score?: number | null
          footprint?: unknown
          id?: string
          item_id?: string
          metadata?: Json
          provider?: string
          sensor?: string
        }
        Relationships: []
      }
      validation_results: {
        Row: {
          case_name: string
          case_type: string
          center_block_percentile: number | null
          concentration_ratio: number | null
          created_at: string
          detected_high_priority: boolean | null
          error_message: string | null
          evaluated_center: unknown
          evaluation_radius_m: number
          fusion_summary: Json
          id: string
          rediscovery_score: number | null
          sensor_summary: Json
          validation_run_id: string
          validation_site_id: string | null
        }
        Insert: {
          case_name: string
          case_type: string
          center_block_percentile?: number | null
          concentration_ratio?: number | null
          created_at?: string
          detected_high_priority?: boolean | null
          error_message?: string | null
          evaluated_center: unknown
          evaluation_radius_m: number
          fusion_summary?: Json
          id?: string
          rediscovery_score?: number | null
          sensor_summary?: Json
          validation_run_id: string
          validation_site_id?: string | null
        }
        Update: {
          case_name?: string
          case_type?: string
          center_block_percentile?: number | null
          concentration_ratio?: number | null
          created_at?: string
          detected_high_priority?: boolean | null
          error_message?: string | null
          evaluated_center?: unknown
          evaluation_radius_m?: number
          fusion_summary?: Json
          id?: string
          rediscovery_score?: number | null
          sensor_summary?: Json
          validation_run_id?: string
          validation_site_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "validation_results_validation_run_id_fkey"
            columns: ["validation_run_id"]
            isOneToOne: false
            referencedRelation: "validation_runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "validation_results_validation_site_id_fkey"
            columns: ["validation_site_id"]
            isOneToOne: false
            referencedRelation: "validation_sites"
            referencedColumns: ["id"]
          },
        ]
      }
      validation_runs: {
        Row: {
          completed_cases: number
          created_at: string
          dataset_version: string
          error_message: string | null
          finished_at: string | null
          git_commit: string | null
          id: string
          metrics: Json
          parameters: Json
          requested_by: string
          split: string
          started_at: string | null
          status: string
          total_cases: number
        }
        Insert: {
          completed_cases?: number
          created_at?: string
          dataset_version: string
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          metrics?: Json
          parameters?: Json
          requested_by: string
          split: string
          started_at?: string | null
          status?: string
          total_cases?: number
        }
        Update: {
          completed_cases?: number
          created_at?: string
          dataset_version?: string
          error_message?: string | null
          finished_at?: string | null
          git_commit?: string | null
          id?: string
          metrics?: Json
          parameters?: Json
          requested_by?: string
          split?: string
          started_at?: string | null
          status?: string
          total_cases?: number
        }
        Relationships: []
      }
      validation_sites: {
        Row: {
          center: unknown
          created_at: string
          dataset_version: string
          evaluation_radius_m: number
          id: string
          latitude: number
          longitude: number
          name: string
          notes: string | null
          public_source: boolean
          region_group: string
          site_type: string
          source_name: string
          source_record_id: string
          source_url: string
          split: string
        }
        Insert: {
          center: unknown
          created_at?: string
          dataset_version: string
          evaluation_radius_m: number
          id?: string
          latitude: number
          longitude: number
          name: string
          notes?: string | null
          public_source?: boolean
          region_group: string
          site_type?: string
          source_name: string
          source_record_id: string
          source_url: string
          split: string
        }
        Update: {
          center?: unknown
          created_at?: string
          dataset_version?: string
          evaluation_radius_m?: number
          id?: string
          latitude?: number
          longitude?: number
          name?: string
          notes?: string | null
          public_source?: boolean
          region_group?: string
          site_type?: string
          source_name?: string
          source_record_id?: string
          source_url?: string
          split?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      create_area_of_interest: {
        Args: { p_geojson: Json; p_name: string; p_project_id: string }
        Returns: {
          area_km2: number
          id: string
        }[]
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
